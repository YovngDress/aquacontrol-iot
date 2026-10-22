import secrets

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models

MAX_IMAGE_UPLOAD_MB = 5


def generate_auth_token():
    return secrets.token_urlsafe(32)


def validate_image_size(value):
    if value.size > MAX_IMAGE_UPLOAD_MB * 1024 * 1024:
        raise ValidationError(f"La imagen no puede pesar más de {MAX_IMAGE_UPLOAD_MB}MB.")


class Profile(models.Model):
    ROLE_GLOBAL = "global"
    ROLE_ADMIN = "admin"
    ROLE_OWNER = "owner"
    ROLE_CHOICES = [
        (ROLE_GLOBAL, "Global"),
        (ROLE_ADMIN, "Administrador"),
        (ROLE_OWNER, "Operador"),
    ]

    user = models.OneToOneField(User, related_name="profile", on_delete=models.CASCADE)
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default=ROLE_OWNER)

    project_name = models.CharField(
        max_length=100,
        blank=True,
        help_text="Nombre de tu proyecto/empresa. Se muestra junto al nombre de la plataforma en tu dashboard.",
    )
    logo = models.ImageField(
        upload_to="branding/user_logos/", blank=True, null=True, validators=[validate_image_size]
    )
    background_image = models.ImageField(
        upload_to="branding/user_backgrounds/", blank=True, null=True, validators=[validate_image_size]
    )

    def __str__(self):
        return f"{self.user.username} ({self.get_role_display()})"


class SiteBranding(models.Model):
    """Singleton: marca general de la plataforma (login y valor por defecto para todos)."""

    logo = models.ImageField(upload_to="branding/site/", blank=True, null=True, validators=[validate_image_size])
    background_image = models.ImageField(
        upload_to="branding/site/", blank=True, null=True, validators=[validate_image_size]
    )
    map_icon = models.ImageField(
        upload_to="branding/site/",
        blank=True,
        null=True,
        validators=[validate_image_size],
        help_text="Ícono exclusivo para los marcadores del Mapa. Independiente del logo de login/dashboard.",
    )

    class Meta:
        verbose_name = "Marca del sitio"
        verbose_name_plural = "Marca del sitio"

    def __str__(self):
        return "Marca del sitio"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass  # el singleton nunca se borra, solo se limpian sus imágenes

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class ProductType(models.Model):
    """Tipo de instalación de la planta (planta de tratamiento, estanque, punto de muestreo, ...).

    Íconos gestionados por el rol Global; el propio rol Global puede agregar
    tipos nuevos (ej. especificados a pedido de un cliente) sin tocar código.
    """

    name = models.CharField(max_length=50, unique=True)
    slug = models.SlugField(unique=True)
    icon = models.ImageField(
        upload_to="branding/product_types/",
        blank=True,
        null=True,
        validators=[validate_image_size],
        help_text="Ícono usado en los marcadores del Mapa para productos de este tipo.",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Product(models.Model):
    """Una instalación concreta (ej. 'Planta de tratamiento Sector Norte'), con 0+ dispositivos."""

    owner = models.ForeignKey(User, related_name="products", on_delete=models.CASCADE)
    product_type = models.ForeignKey(ProductType, related_name="products", on_delete=models.PROTECT)
    name = models.CharField(max_length=100)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    icon = models.ImageField(
        upload_to="branding/product_icons/",
        blank=True,
        null=True,
        validators=[validate_image_size],
        help_text=(
            "Ícono exclusivo de este producto para el Mapa (opcional). Si no se define, se usa el "
            "ícono de su tipo. Solo el rol Global puede editarlo."
        ),
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.product_type.name})"

    @property
    def has_location(self):
        return self.latitude is not None and self.longitude is not None

    @property
    def map_icon(self):
        if self.icon:
            return self.icon
        return self.product_type.icon


class ManualEntry(models.Model):
    """Registro a mano del estado de un producto sin dispositivo/sensor asociado."""

    product = models.ForeignKey(Product, related_name="manual_entries", on_delete=models.CASCADE)
    note = models.TextField(help_text="Ej. «Muestra de laboratorio: cloro libre 0.6 mg/L, dentro de NCh 409».")
    variable = models.CharField(max_length=50, blank=True, help_text="Ej. cloro_libre (opcional).")
    value = models.FloatField(null=True, blank=True)
    unit = models.CharField(max_length=20, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "Manual entries"

    def __str__(self):
        return f"{self.product.name}: {self.note[:40]}"


class Device(models.Model):
    owner = models.ForeignKey(User, related_name="devices", on_delete=models.CASCADE)
    product = models.ForeignKey(
        Product,
        related_name="devices",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Instalación de la planta a la que pertenece este dispositivo (opcional).",
    )
    name = models.CharField(max_length=100)
    slug = models.SlugField(
        unique=True,
        help_text="ID del dispositivo. Debe coincidir con el segmento del topic MQTT: <prefijo>/<slug>/<variable>",
    )
    tag = models.CharField(
        max_length=50,
        blank=True,
        help_text="Tipo de hardware: ESP32, PLC o Raspberry Pi (se muestra en el dashboard).",
    )
    auth_token = models.CharField(
        max_length=64,
        unique=True,
        editable=False,
        default=generate_auth_token,
        help_text="Token único que debe programarse en el firmware del dispositivo y enviarse en cada mensaje MQTT.",
    )
    location = models.CharField(max_length=200, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def regenerate_token(self):
        self.auth_token = generate_auth_token()
        self.save(update_fields=["auth_token"])

    @property
    def has_location(self):
        return self.latitude is not None and self.longitude is not None

    def sensors_in_plant_order(self):
        """Sensores activos en el orden del catálogo de la planta (plant.py)."""
        from .plant import VARIABLES

        order = {name: i for i, name in enumerate(VARIABLES)}
        return sorted(
            (s for s in self.sensors.all() if s.is_active),
            key=lambda s: (order.get(s.variable, len(order)), s.variable),
        )


class Sensor(models.Model):
    device = models.ForeignKey(Device, related_name="sensors", on_delete=models.CASCADE)
    variable = models.CharField(max_length=50, help_text="ej. ph, turbidez, cloro_libre, nivel_estanque")
    unit = models.CharField(max_length=20, blank=True, help_text="ej. pH, NTU, mg/L")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("device", "variable")
        ordering = ["device__name", "variable"]

    def __str__(self):
        return f"{self.device.name} / {self.variable}"

    def get_widget(self):
        from .plant import widget_defaults

        widget, _ = Widget.objects.get_or_create(sensor=self, defaults=widget_defaults(self.variable))
        return widget

    @property
    def label(self):
        from .plant import label_for

        return label_for(self.variable)

    @property
    def range_text(self):
        """Rango NCh 409 (o de referencia operacional) de esta variable, si tiene."""
        from .plant import get_spec, range_text

        spec = get_spec(self.variable)
        bounds = spec.get("norm") or spec.get("ref")
        return range_text(bounds) if bounds else ""

    @property
    def is_regulated(self):
        from .plant import get_spec

        return "norm" in get_spec(self.variable)


class Widget(models.Model):
    TYPE_LINE_CHART = "line_chart"
    TYPE_NUMERIC = "numeric"
    TYPE_METER = "meter"
    TYPE_LED = "led"
    TYPE_BUTTON = "button"
    TYPE_SLIDER = "slider"
    TYPE_CHOICES = [
        (TYPE_LINE_CHART, "Gráfico de línea"),
        (TYPE_NUMERIC, "Valor numérico"),
        (TYPE_METER, "Medidor (barra)"),
        (TYPE_LED, "Indicador LED"),
        (TYPE_BUTTON, "Botón (control)"),
        (TYPE_SLIDER, "Slider (control)"),
    ]
    CONTROL_TYPES = {TYPE_BUTTON, TYPE_SLIDER}

    sensor = models.OneToOneField(Sensor, related_name="widget", on_delete=models.CASCADE)
    widget_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default=TYPE_LINE_CHART)

    min_value = models.FloatField(default=0, help_text="Usado por Medidor y Slider.")
    max_value = models.FloatField(default=100, help_text="Usado por Medidor y Slider.")
    led_on_threshold = models.FloatField(
        default=1,
        help_text=(
            "Usado por Indicador LED: se enciende cuando el valor es mayor o igual a este umbral "
            "(ej. alarma de planta = 1)."
        ),
    )
    button_on_value = models.FloatField(default=1, help_text="Usado por Botón: valor que se publica al encender.")
    button_off_value = models.FloatField(default=0, help_text="Usado por Botón: valor que se publica al apagar.")
    slider_step = models.FloatField(default=1, help_text="Usado por Slider.")

    def __str__(self):
        return f"{self.sensor} ({self.get_widget_type_display()})"

    @property
    def is_control(self):
        return self.widget_type in self.CONTROL_TYPES


class SensorReading(models.Model):
    sensor = models.ForeignKey(Sensor, related_name="readings", on_delete=models.CASCADE)
    value = models.FloatField()
    timestamp = models.DateTimeField(db_index=True)

    class Meta:
        indexes = [models.Index(fields=["sensor", "-timestamp"])]
        ordering = ["-timestamp"]

    def __str__(self):
        return f"{self.sensor} = {self.value} @ {self.timestamp}"


class UnregisteredReading(models.Model):
    """Cuarentena para mensajes MQTT rechazados por identidad.

    Evita crear Devices "fantasma" a partir de topics con typos o clientes
    no autorizados que conozcan la contraseña MQTT compartida, y detecta
    intentos de suplantación con un token inválido.
    """

    REASON_UNKNOWN_DEVICE = "unknown_device"
    REASON_INVALID_TOKEN = "invalid_token"
    REASON_CHOICES = [
        (REASON_UNKNOWN_DEVICE, "Dispositivo desconocido"),
        (REASON_INVALID_TOKEN, "Token inválido"),
    ]

    topic = models.CharField(max_length=255)
    raw_payload = models.TextField()
    reason = models.CharField(max_length=30, choices=REASON_CHOICES, default=REASON_UNKNOWN_DEVICE)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-received_at"]

    def __str__(self):
        return f"{self.topic} @ {self.received_at}"

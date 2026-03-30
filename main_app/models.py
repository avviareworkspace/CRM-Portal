from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import UserManager
from django.dispatch import receiver
from django.db.models.signals import post_save
from django.db import models
from django.contrib.auth.models import AbstractUser
from django.utils import timezone
from datetime import datetime, timedelta
import uuid
import logging


class CustomUserManager(UserManager):
    def _create_user(self, email, password, **extra_fields):
        email = self.normalize_email(email)
        user = CustomUser(email=email, **extra_fields)
        user.password = make_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        # CRM Admin profile + permissions use user_type '1' (see ensure_admin_profile).
        extra_fields.setdefault("user_type", "1")
        extra_fields.setdefault("gender", "M")
        extra_fields.setdefault("address", "")
        extra_fields.setdefault("first_name", "")
        extra_fields.setdefault("last_name", "")

        assert extra_fields["is_staff"]
        assert extra_fields["is_superuser"]
        return self._create_user(email, password, **extra_fields)


class CustomUser(AbstractUser):
    USER_TYPE = ((1, "Admin"), (2, "Counsellor"))
    GENDER = [("M", "Male"), ("F", "Female")]
    
    username = None  # Removed username, using email instead
    email = models.EmailField(unique=True)
    user_type = models.CharField(default=1, choices=USER_TYPE, max_length=1, db_index=True)
    gender = models.CharField(max_length=1, choices=GENDER)
    profile_pic = models.ImageField(upload_to='profile_pics/', null=True, blank=True)
    address = models.TextField()
    phone = models.CharField(max_length=15, blank=True)
    fcm_token = models.TextField(default="")  # For firebase notifications
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []
    objects = CustomUserManager()

    def __str__(self):
        return self.first_name + " " + self.last_name


class Admin(models.Model):
    admin = models.OneToOneField(CustomUser, on_delete=models.CASCADE)
    is_superadmin = models.BooleanField(default=True, help_text="Full access to everything")
    can_delete = models.BooleanField(default=True, help_text="Can delete leads, activities, etc.")
    can_view_performance = models.BooleanField(default=True, help_text="Can view counsellor performance")
    can_view_counsellor_work = models.BooleanField(default=True, help_text="Can view counsellor work details")
    can_manage_settings = models.BooleanField(default=True, help_text="Can manage lead sources, statuses, activity types, etc.")

    def __str__(self):
        return self.admin.first_name + " " + self.admin.last_name

    def has_perm_delete(self):
        return self.is_superadmin or self.can_delete

    def has_perm_performance(self):
        return self.is_superadmin or self.can_view_performance

    def has_perm_counsellor_work(self):
        return self.is_superadmin or self.can_view_counsellor_work

    def has_perm_settings(self):
        return self.is_superadmin or self.can_manage_settings


class Counsellor(models.Model):
    admin = models.OneToOneField(CustomUser, on_delete=models.CASCADE)
    employee_id = models.CharField(max_length=20, unique=True)
    department = models.CharField(max_length=100, blank=True)
    joining_date = models.DateField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    performance_rating = models.DecimalField(max_digits=3, decimal_places=2, default=0.00)
    total_leads_assigned = models.IntegerField(default=0)
    total_business_generated = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)

    def __str__(self):
        return f"{self.admin.first_name} {self.admin.last_name} ({self.employee_id})"


class LeadSource(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class LeadStatus(models.Model):
    """Configurable lead statuses managed from admin panel (like LeadSource)."""
    code = models.CharField(max_length=30, unique=True, help_text="Internal code e.g. NEW, CONTACTED")
    name = models.CharField(max_length=100, help_text="Display name e.g. 'New', 'Contacted'")
    description = models.TextField(blank=True)
    color = models.CharField(max_length=20, default='secondary', help_text="Bootstrap badge color class e.g. info, success, danger")
    is_active = models.BooleanField(default=True)
    is_system = models.BooleanField(default=False, help_text="System statuses cannot be deleted")
    sort_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['sort_order', 'name']
        verbose_name_plural = 'Lead Statuses'

    def __str__(self):
        return self.name

    @classmethod
    def get_choices(cls):
        """Return list of (code, name) tuples for form/model choices, active only."""
        return list(cls.objects.filter(is_active=True).order_by('sort_order', 'name').values_list('code', 'name'))

    @classmethod
    def get_all_choices(cls):
        """Return all (code, name) tuples including inactive, for display purposes."""
        return list(cls.objects.order_by('sort_order', 'name').values_list('code', 'name'))


# Hardcoded fallback – used only when LeadStatus table is empty (fresh install)
DEFAULT_LEAD_STATUSES = (
    ('NEW', 'New'),
    ('CONTACTED', 'Contacted'),
    ('QUALIFIED', 'Qualified'),
    ('PROPOSAL_SENT', 'Proposal Sent'),
    ('NEGOTIATION', 'Negotiation'),
    ('CLOSED_WON', 'Closed Won'),
    ('CLOSED_LOST', 'Closed Lost'),
    ('TRANSFERRED', 'Transferred'),
)


class Lead(models.Model):
    LEAD_STATUS = DEFAULT_LEAD_STATUSES
    
    PRIORITY = (
        ('LOW', 'Low'),
        ('MEDIUM', 'Medium'),
        ('HIGH', 'High'),
        ('URGENT', 'Urgent')
    )

    lead_id = models.CharField(max_length=20, unique=True, blank=True)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField()
    phone = models.CharField(max_length=15)
    alternate_phone = models.CharField(max_length=15, blank=True, verbose_name="Alternate Phone")
    school_name = models.CharField(max_length=200, blank=True, verbose_name="School Name")
    position = models.CharField(max_length=100, blank=True)
    GRADUATION_CHOICES = (
        ('YES', 'Yes'),
        ('NO', 'No'),
    )
    
    # Map to existing database columns
    graduation_status = models.CharField(max_length=3, choices=GRADUATION_CHOICES, default='NO', verbose_name="Graduation Status")
    graduation_course = models.CharField(max_length=200, blank=True, default='Not Specified', verbose_name="Graduation Course")
    graduation_year = models.IntegerField(null=True, blank=True, verbose_name="Graduation Year")
    graduation_college = models.CharField(max_length=200, blank=True, default='Not Specified', verbose_name="Graduation College", db_column='college_name')  # Maps to college_name in DB
    course_interested = models.CharField(max_length=200, blank=True, verbose_name="Course Interested In")
    is_graduated = models.CharField(max_length=3, choices=GRADUATION_CHOICES, default='NO', verbose_name="Is Graduated")  # Maps to existing is_graduated field
    
    # Keep industry field for backward compatibility but make it hidden
    industry = models.CharField(max_length=100, blank=True, verbose_name="Industry (Legacy)")
    source = models.ForeignKey(LeadSource, on_delete=models.PROTECT)
    status = models.CharField(max_length=20, choices=LEAD_STATUS, default='NEW', db_index=True)
    priority = models.CharField(max_length=10, choices=PRIORITY, default='MEDIUM', db_index=True)
    assigned_counsellor = models.ForeignKey(Counsellor, on_delete=models.SET_NULL, null=True, blank=True)
    previous_counsellor = models.ForeignKey(Counsellor, on_delete=models.SET_NULL, null=True, blank=True, related_name='previous_leads')
    expected_value = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    actual_value = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    notes = models.TextField(blank=True)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, blank=True)
    postal_code = models.CharField(max_length=30, blank=True)
    website = models.URLField(blank=True)
    linkedin = models.URLField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_contact_date = models.DateTimeField(null=True, blank=True, db_index=True)
    next_follow_up = models.DateTimeField(null=True, blank=True, db_index=True)
    # AI-evaluated probability of conversion (0-100)
    conversion_score = models.IntegerField(null=True, blank=True)
    # AI enrichment and routing
    enriched_job_title = models.CharField(max_length=150, blank=True)
    enrichment_notes = models.TextField(blank=True)
    routed_to = models.CharField(max_length=100, blank=True)
    routing_reason = models.TextField(blank=True)

    def __str__(self):
        return f"{self.first_name} {self.last_name} - {self.school_name}"

    def save(self, *args, **kwargs):
        if not self.lead_id:
            # Generate shorter lead_id: L-YYMMDD-XXXX (max 12 chars)
            self.lead_id = f"L-{datetime.now().strftime('%y%m%d')}-{uuid.uuid4().hex[:4].upper()}"
        
        # Set is_graduated based on graduation_status
        if self.graduation_status == 'YES':
            self.is_graduated = 'YES'
        else:
            self.is_graduated = 'NO'
            
        super().save(*args, **kwargs)


class LeadAlternatePhone(models.Model):
    """
    Additional alternate phone numbers for a lead, maintained by counsellors.
    This lets counsellors add multiple contact numbers without editing core lead fields.
    """
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='alternate_phones')
    phone = models.CharField(max_length=20)
    label = models.CharField(max_length=50, blank=True, help_text="Eg. Father, Mother, Guardian")
    created_by = models.ForeignKey(Counsellor, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    def __str__(self):
        return f"{self.lead.lead_id} - {self.phone} ({self.label or 'alternate'})"


class ActivityType(models.Model):
    """Configurable activity types managed from admin panel."""
    code = models.CharField(max_length=30, unique=True, help_text="Internal code e.g. CALL, EMAIL, MEETING")
    name = models.CharField(max_length=100, help_text="Display name e.g. 'Phone Call', 'Email'")
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=50, default='fas fa-tasks', help_text="FontAwesome icon class")
    color = models.CharField(max_length=20, default='info', help_text="Bootstrap badge color class")
    is_active = models.BooleanField(default=True)
    is_system = models.BooleanField(default=False, help_text="System types cannot be deleted")
    sort_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['sort_order', 'name']
        verbose_name_plural = 'Activity Types'

    def __str__(self):
        return self.name

    @classmethod
    def get_choices(cls):
        return list(cls.objects.filter(is_active=True).order_by('sort_order', 'name').values_list('code', 'name'))

    @classmethod
    def get_all_choices(cls):
        return list(cls.objects.order_by('sort_order', 'name').values_list('code', 'name'))


class NextAction(models.Model):
    """Configurable next-action options managed from admin panel."""
    code = models.CharField(max_length=30, unique=True, help_text="Internal code e.g. CALLBACK, SEND_BROCHURE")
    name = models.CharField(max_length=100, help_text="Display name e.g. 'Callback', 'Send Brochure'")
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    is_system = models.BooleanField(default=False)
    sort_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['sort_order', 'name']
        verbose_name_plural = 'Next Actions'

    def __str__(self):
        return self.name

    @classmethod
    def get_choices(cls):
        return [('', '— None —')] + list(cls.objects.filter(is_active=True).order_by('sort_order', 'name').values_list('code', 'name'))

    @classmethod
    def get_all_choices(cls):
        return list(cls.objects.order_by('sort_order', 'name').values_list('code', 'name'))


DEFAULT_ACTIVITY_TYPES = (
    ('CALL', 'Phone Call'),
    ('EMAIL', 'Email'),
    ('MEETING', 'Meeting'),
    ('PROPOSAL', 'Proposal Sent'),
    ('FOLLOW_UP', 'Visit'),
    ('TRANSFER', 'Lead Transfer'),
    ('NOTE', 'Note'),
)


class LeadActivity(models.Model):
    ACTIVITY_TYPE = DEFAULT_ACTIVITY_TYPES

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='activities')
    counsellor = models.ForeignKey(Counsellor, on_delete=models.CASCADE)
    activity_type = models.CharField(max_length=30, choices=ACTIVITY_TYPE, db_index=True)
    subject = models.CharField(max_length=200)
    description = models.TextField()
    outcome = models.CharField(max_length=200, blank=True)
    next_action = models.CharField(max_length=200, blank=True)
    scheduled_date = models.DateTimeField(null=True, blank=True, db_index=True)
    completed_date = models.DateTimeField(auto_now_add=True, db_index=True)
    duration = models.IntegerField(default=0)  # in minutes
    is_completed = models.BooleanField(default=True, db_index=True)

    def __str__(self):
        return f"{self.lead.first_name} - {self.activity_type} - {self.subject}"


class Business(models.Model):
    BUSINESS_STATUS = (
        ('PENDING', 'Pending'),
        ('ACTIVE', 'Active'),
        ('COMPLETED', 'Completed'),
        ('CANCELLED', 'Cancelled')
    )

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='businesses')
    counsellor = models.ForeignKey(Counsellor, on_delete=models.CASCADE)
    business_id = models.CharField(max_length=20, unique=True, blank=True)
    title = models.CharField(max_length=200)
    description = models.TextField()
    value = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=20, choices=BUSINESS_STATUS, default='PENDING', db_index=True)
    start_date = models.DateField(db_index=True)
    end_date = models.DateField(null=True, blank=True)
    payment_terms = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.title} - {self.lead.first_name} {self.lead.last_name}"

    def save(self, *args, **kwargs):
        if not self.business_id:
            self.business_id = f"BUS-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"
        super().save(*args, **kwargs)


class NotificationCounsellor(models.Model):
    counsellor = models.ForeignKey(Counsellor, on_delete=models.CASCADE)
    message = models.TextField()
    is_read = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.counsellor.admin.first_name} - {self.message[:50]}"


class NotificationAdmin(models.Model):
    admin = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='admin_notifications', null=True, blank=True)
    message = models.TextField()
    is_read = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        if self.admin:
            return f"{self.admin.first_name} - {self.message[:50]}"
        return f"Admin - {self.message[:50]}"


class LeadTransfer(models.Model):
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE)
    from_counsellor = models.ForeignKey(Counsellor, on_delete=models.CASCADE, related_name='transfers_from')
    to_counsellor = models.ForeignKey(Counsellor, on_delete=models.CASCADE, related_name='transfers_to')
    reason = models.TextField()
    admin_approved = models.BooleanField(default=False)
    approved_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.lead.first_name} {self.lead.last_name} - {self.from_counsellor} to {self.to_counsellor}"


class CounsellorPerformance(models.Model):
    counsellor = models.ForeignKey(Counsellor, on_delete=models.CASCADE)
    month = models.DateField()  # First day of the month
    total_leads_assigned = models.IntegerField(default=0)
    total_leads_contacted = models.IntegerField(default=0)
    total_leads_qualified = models.IntegerField(default=0)
    total_business_generated = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    conversion_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0.00)
    average_response_time = models.IntegerField(default=0)  # in hours
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['counsellor', 'month']

    def __str__(self):
        return f"{self.counsellor.admin.first_name} - {self.month.strftime('%B %Y')}"


class DataAccessLog(models.Model):
    """
    Lightweight audit log for potentially sensitive data access.
    Used to detect unusual behaviour (eg. many lead views in short time)
    and to provide forensic evidence in case of data leaks.
    """
    ACTION_CHOICES = (
        ('view_lead_detail', 'View lead detail'),
        ('list_my_leads', 'List my leads'),
        ('view_business_detail', 'View business detail'),
        ('reveal_phone', 'Reveal phone'),
        ('reveal_alternate_phone', 'Reveal alternate phone'),
    )

    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE)
    counsellor = models.ForeignKey(Counsellor, null=True, blank=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=50, choices=ACTION_CHOICES, db_index=True)
    lead = models.ForeignKey(Lead, null=True, blank=True, on_delete=models.SET_NULL)
    business = models.ForeignKey(Business, null=True, blank=True, on_delete=models.SET_NULL)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=['action', 'created_at']),
            models.Index(fields=['counsellor', 'created_at']),
        ]

    def __str__(self):
        target = self.lead or self.business
        return f"{self.user.email} - {self.action} - {target or 'n/a'}"


class DailyTarget(models.Model):
    """
    Simple daily task target: admin sets a number, system auto-prioritises.
    Priority order: today's visits → pending activities → leads by status (NEW last).
    """
    target_date = models.DateField(db_index=True)
    target_count = models.PositiveIntegerField(help_text="Total tasks to show (e.g. 100)")
    created_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, related_name='created_targets')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-target_date']

    def __str__(self):
        return f"{self.target_count} tasks — {self.target_date}"


class DailyTargetAssignment(models.Model):
    """Links a DailyTarget to one or more counsellors."""
    target = models.ForeignKey(DailyTarget, on_delete=models.CASCADE, related_name='assignments')
    counsellor = models.ForeignKey(Counsellor, on_delete=models.CASCADE, related_name='daily_targets')
    completed_count = models.IntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('target', 'counsellor')

    def __str__(self):
        return f"{self.counsellor.admin.first_name} — {self.target}"


class MetaIntegrationSettings(models.Model):
    """
    Single row (pk=1): Meta WhatsApp Cloud API + Instagram + Facebook Page (Messenger) webhooks.
    Secrets are stored in the database; restrict DB access in production. Env vars override when set.
    """

    public_base_url = models.CharField(
        max_length=500,
        blank=True,
        help_text="Public site URL, no trailing slash (e.g. https://crm.example.com). Used to show the webhook URL.",
    )
    verify_token = models.CharField(
        max_length=255,
        blank=True,
        help_text="Same token you enter in Meta Developer → Webhooks → Verify token.",
    )
    app_secret = models.CharField(
        max_length=255,
        blank=True,
        help_text="Meta App Secret (for X-Hub-Signature-256). Strongly recommended in production.",
    )
    access_token = models.TextField(
        blank=True,
        help_text="System User or permanent access token for sending WhatsApp replies via Graph API.",
    )
    whatsapp_phone_number_id = models.CharField(
        max_length=64,
        blank=True,
        help_text="WhatsApp → API Setup → Phone number ID.",
    )
    facebook_page_id = models.CharField(
        max_length=64,
        blank=True,
        help_text="Facebook Page ID for sending Messenger/Instagram replies (Graph API).",
    )
    whatsapp_enabled = models.BooleanField(default=False)
    instagram_enabled = models.BooleanField(default=False)
    facebook_messenger_enabled = models.BooleanField(
        default=False,
        help_text="Inbound Facebook Page messages (Messenger) — webhook object type “page”.",
    )
    notify_admins_on_message = models.BooleanField(
        default=True,
        help_text="Create an admin notification for each inbound WhatsApp / Instagram / Facebook message.",
    )

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Meta / WhatsApp / Instagram / Facebook integration"
        verbose_name_plural = "Meta / WhatsApp / Instagram / Facebook integration"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(
            pk=1,
            defaults={
                "verify_token": "",
                "public_base_url": "",
            },
        )
        return obj


class SocialChatThread(models.Model):
    """One conversation per channel + external user (WhatsApp wa_id or Messenger PSID / IG id)."""

    CHANNEL_WHATSAPP = "whatsapp"
    CHANNEL_INSTAGRAM = "instagram"
    CHANNEL_FACEBOOK = "facebook"
    CHANNEL_CHOICES = (
        (CHANNEL_WHATSAPP, "WhatsApp"),
        (CHANNEL_INSTAGRAM, "Instagram"),
        (CHANNEL_FACEBOOK, "Facebook"),
    )

    channel = models.CharField(max_length=20, choices=CHANNEL_CHOICES, db_index=True)
    external_user_id = models.CharField(
        max_length=128,
        db_index=True,
        help_text="WhatsApp wa_id or Messenger/Instagram scoped user id.",
    )
    page_or_waba_id = models.CharField(
        max_length=128,
        blank=True,
        help_text="Facebook Page id or WABA id from webhook entry (for routing sends).",
    )
    display_name = models.CharField(max_length=200, blank=True)
    lead = models.ForeignKey(
        "Lead",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="social_threads",
    )
    last_message_at = models.DateTimeField(db_index=True, default=timezone.now)
    last_message_preview = models.CharField(max_length=300, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-last_message_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["channel", "external_user_id", "page_or_waba_id"],
                name="uniq_social_thread_channel_user_page",
            ),
        ]
        indexes = [
            models.Index(fields=["channel", "last_message_at"]),
        ]

    def __str__(self):
        return f"{self.get_channel_display()} {self.display_name or self.external_user_id}"


class SocialChatMessage(models.Model):
    DIRECTION_IN = "in"
    DIRECTION_OUT = "out"
    DIRECTION_CHOICES = ((DIRECTION_IN, "Inbound"), (DIRECTION_OUT, "Outbound"))

    thread = models.ForeignKey(
        SocialChatThread,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    direction = models.CharField(max_length=3, choices=DIRECTION_CHOICES, db_index=True)
    body = models.TextField()
    external_message_id = models.CharField(max_length=128, blank=True)
    error_detail = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["thread", "created_at"]),
        ]

    def __str__(self):
        return f"{self.direction} @ {self.created_at}"


def _is_admin_user_type(user_type) -> bool:
    return str(user_type) == "1"


@receiver(post_save, sender=CustomUser)
def ensure_admin_profile(sender, instance, **kwargs):
    """Create or fix main_app.Admin for CRM admins (user_type 1), including createsuperuser."""
    if not _is_admin_user_type(instance.user_type):
        return
    profile, created = Admin.objects.get_or_create(
        admin=instance,
        defaults={
            "is_superadmin": instance.is_superuser,
            "can_delete": True,
            "can_view_performance": True,
            "can_view_counsellor_work": True,
            "can_manage_settings": True,
        },
    )
    if instance.is_superuser and not profile.is_superadmin:
        profile.is_superadmin = True
        profile.can_delete = True
        profile.can_view_performance = True
        profile.can_view_counsellor_work = True
        profile.can_manage_settings = True
        profile.save(
            update_fields=[
                "is_superadmin",
                "can_delete",
                "can_view_performance",
                "can_view_counsellor_work",
                "can_manage_settings",
            ]
        )
    # Counsellor profiles stay manual in views (employee_id, etc.)


@receiver(post_save, sender=CustomUser)
def save_user_profile(sender, instance, **kwargs):
    try:
        if _is_admin_user_type(instance.user_type):
            if hasattr(instance, "admin"):
                instance.admin.save()
        if instance.user_type == '2':
            if hasattr(instance, 'counsellor'):
                instance.counsellor.save()
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.error(f"Error saving user profile: {e}")

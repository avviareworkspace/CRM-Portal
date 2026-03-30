from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.forms.widgets import DateInput, TextInput, DateTimeInput
from .models import *
import os


class FormSettings(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super(FormSettings, self).__init__(*args, **kwargs)
        for field in self.visible_fields():
            field.field.widget.attrs['class'] = 'form-control'


class CustomUserForm(FormSettings):
    email = forms.EmailField(required=True)
    gender = forms.ChoiceField(choices=[('M', 'Male'), ('F', 'Female')])
    first_name = forms.CharField(required=True)
    last_name = forms.CharField(required=True)
    address = forms.CharField(widget=forms.Textarea)
    phone = forms.CharField(max_length=15, required=False)
    password = forms.CharField(widget=forms.PasswordInput)
    widget = {
        'password': forms.PasswordInput(),
    }
    profile_pic = forms.ImageField(required=False)

    def _customuser_for_form(self):
        """Return the CustomUser row; instance may be CustomUser or Admin/Counsellor profile."""
        inst = self.instance
        if isinstance(inst, CustomUser):
            return inst
        if isinstance(inst, (Admin, Counsellor)):
            return inst.admin
        return inst

    def __init__(self, *args, **kwargs):
        super(CustomUserForm, self).__init__(*args, **kwargs)

        user = self._customuser_for_form()
        if user is not None and getattr(user, 'pk', None):
            self.fields['password'].required = False
            for field in CustomUserForm.Meta.fields:
                if hasattr(user, field):
                    self.fields[field].initial = getattr(user, field)
            self.fields['password'].widget.attrs['placeholder'] = "Fill this only if you wish to update password"

    def save(self, commit=True):
        """Ensure passwords are hashed on create/update.

        - On create: require password and hash it
        - On update: hash only if a new password was provided; otherwise keep existing
        """
        instance = super(CustomUserForm, self).save(commit=False)
        password = self.cleaned_data.get('password')

        # When creating a new user, password is required by the form; hash it
        if not instance.pk and password:
            from django.contrib.auth.hashers import make_password
            instance.password = make_password(password)

        # When updating existing user, hash only if password field was changed/provided
        if instance.pk:
            if password:
                from django.contrib.auth.hashers import make_password
                instance.password = make_password(password)

        if commit:
            instance.save()
        return instance

    def clean_profile_pic(self):
        picture = self.cleaned_data.get('profile_pic')
        if not picture:
            return picture

        max_size_mb = getattr(settings, 'MAX_PROFILE_PIC_MB', 5)
        if picture.size > max_size_mb * 1024 * 1024:
            raise ValidationError(f"Profile picture too large. Max size is {max_size_mb}MB.")

        content_type = getattr(picture, 'content_type', '') or ''
        if not content_type.startswith('image/'):
            raise ValidationError("Invalid file type. Please upload an image.")

        ext = os.path.splitext(picture.name)[1].lower()
        allowed_exts = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}
        if ext not in allowed_exts:
            raise ValidationError("Unsupported image format. Allowed: JPG, PNG, GIF, WEBP.")

        return picture

    def clean_email(self, *args, **kwargs):
        formEmail = self.cleaned_data['email'].lower()
        if self.instance.pk is None:  # Insert
            if CustomUser.objects.filter(email=formEmail).exists():
                raise forms.ValidationError(
                    "The given email is already registered")
        else:  # Update
            dbEmail = self._customuser_for_form().email.lower()
            
            if dbEmail != formEmail:  # There has been changes
                if CustomUser.objects.filter(email=formEmail).exists():
                    raise forms.ValidationError("The given email is already registered")

        return formEmail

    class Meta:
        model = CustomUser
        fields = ['first_name', 'last_name', 'email', 'gender', 'phone', 'password', 'profile_pic', 'address']


class CounsellorForm(CustomUserForm):
    employee_id = forms.CharField(max_length=20, required=True)
    department = forms.CharField(max_length=100, required=False)
    
    def __init__(self, *args, **kwargs):
        super(CounsellorForm, self).__init__(*args, **kwargs)

    class Meta(CustomUserForm.Meta):
        model = CustomUser
        fields = CustomUserForm.Meta.fields + ['employee_id', 'department']


class AdminForm(CustomUserForm):
    def __init__(self, *args, **kwargs):
        # If editing an existing admin, convert Admin instance to CustomUser instance
        # This must be done BEFORE calling super() so ModelForm sets self.instance correctly
        if 'instance' in kwargs and kwargs['instance'] is not None:
            instance = kwargs['instance']
            # Check if this is an Admin model instance (not CustomUser)
            if isinstance(instance, Admin):
                kwargs['instance'] = instance.admin
        super(AdminForm, self).__init__(*args, **kwargs)

    class Meta(CustomUserForm.Meta):
        model = CustomUser
        fields = CustomUserForm.Meta.fields


class LeadSourceForm(FormSettings):
    def __init__(self, *args, **kwargs):
        super(LeadSourceForm, self).__init__(*args, **kwargs)

    class Meta:
        model = LeadSource
        fields = ['name', 'description', 'is_active']


class LeadStatusForm(FormSettings):
    BADGE_COLOR_CHOICES = [
        ('info', 'Info (Blue)'),
        ('primary', 'Primary (Dark Blue)'),
        ('warning', 'Warning (Yellow)'),
        ('success', 'Success (Green)'),
        ('danger', 'Danger (Red)'),
        ('secondary', 'Secondary (Grey)'),
        ('dark', 'Dark'),
        ('light', 'Light'),
    ]

    color = forms.ChoiceField(choices=BADGE_COLOR_CHOICES)

    def __init__(self, *args, **kwargs):
        super(LeadStatusForm, self).__init__(*args, **kwargs)
        self.fields['code'].help_text = 'Internal code (uppercase, no spaces, e.g. FOLLOW_UP)'

    class Meta:
        model = LeadStatus
        fields = ['code', 'name', 'description', 'color', 'sort_order', 'is_active']


class ActivityTypeForm(FormSettings):
    ICON_CHOICES = [
        ('fas fa-phone', 'Phone'),
        ('fas fa-envelope', 'Envelope'),
        ('fas fa-handshake', 'Handshake / Meeting'),
        ('fas fa-file-alt', 'Document / Proposal'),
        ('fas fa-calendar-check', 'Calendar / Visit'),
        ('fas fa-exchange-alt', 'Transfer'),
        ('fas fa-sticky-note', 'Note'),
        ('fas fa-tasks', 'Tasks (default)'),
        ('fas fa-comments', 'Chat / Discussion'),
        ('fas fa-video', 'Video Call'),
    ]
    BADGE_COLOR_CHOICES = [
        ('info', 'Info (Blue)'),
        ('primary', 'Primary (Dark Blue)'),
        ('warning', 'Warning (Yellow)'),
        ('success', 'Success (Green)'),
        ('danger', 'Danger (Red)'),
        ('secondary', 'Secondary (Grey)'),
        ('dark', 'Dark'),
    ]

    icon = forms.ChoiceField(choices=ICON_CHOICES)
    color = forms.ChoiceField(choices=BADGE_COLOR_CHOICES)

    def __init__(self, *args, **kwargs):
        super(ActivityTypeForm, self).__init__(*args, **kwargs)
        self.fields['code'].help_text = 'Uppercase, no spaces (e.g. SITE_VISIT)'

    class Meta:
        model = ActivityType
        fields = ['code', 'name', 'description', 'icon', 'color', 'sort_order', 'is_active']


class NextActionForm(FormSettings):
    def __init__(self, *args, **kwargs):
        super(NextActionForm, self).__init__(*args, **kwargs)
        self.fields['code'].help_text = 'Uppercase, no spaces (e.g. SEND_BROCHURE)'

    class Meta:
        model = NextAction
        fields = ['code', 'name', 'description', 'sort_order', 'is_active']


class LeadForm(FormSettings):
    def __init__(self, *args, **kwargs):
        super(LeadForm, self).__init__(*args, **kwargs)
        # Hide the old industry field
        self.fields['industry'].widget = forms.HiddenInput()
        # Hide the is_graduated field (will be set automatically)
        self.fields['is_graduated'].widget = forms.HiddenInput()
        # Set up graduation year field
        self.fields['graduation_year'].widget = forms.NumberInput(attrs={'min': '1950', 'max': '2030'})
        # Make graduation fields optional
        self.fields['graduation_course'].required = False
        self.fields['graduation_college'].required = False
        self.fields['is_graduated'].required = False

    class Meta:
        model = Lead
        fields = [
            'first_name', 'last_name', 'email', 'phone', 'alternate_phone', 'school_name', 'position', 
            'graduation_status', 'graduation_course', 'graduation_year', 'graduation_college',
            'course_interested', 'is_graduated', 'industry', 'source', 'status', 'priority', 'assigned_counsellor',
            'notes', 'address', 'city', 'state', 'country', 
            'postal_code', 'website', 'linkedin', 'next_follow_up'
        ]
        widgets = {
            'next_follow_up': DateTimeInput(attrs={'type': 'datetime-local'}),
        }


class CounsellorLeadForm(FormSettings):
    """
    Simplified lead form for counsellors to update key contact and follow-up details
    without changing routing/source fields reserved for admins.
    """
    def __init__(self, *args, **kwargs):
        super(CounsellorLeadForm, self).__init__(*args, **kwargs)
        self.fields['graduation_year'].widget = forms.NumberInput(attrs={'min': '1950', 'max': '2030'})
        self.fields['graduation_course'].required = False
        self.fields['graduation_college'].required = False

    class Meta:
        model = Lead
        fields = [
            'first_name', 'last_name', 'email', 'phone', 'alternate_phone', 'school_name',
            'graduation_status', 'graduation_course', 'graduation_year', 'graduation_college',
            'course_interested', 'status', 'priority',
            'notes', 'address', 'city', 'state', 'country',
            'postal_code', 'next_follow_up'
        ]
        widgets = {
            'next_follow_up': DateTimeInput(attrs={'type': 'datetime-local'}),
        }


class LeadActivityForm(FormSettings):
    HAS_NEXT_ACTION_CHOICES = [('no', 'No'), ('yes', 'Yes')]

    has_next_action = forms.ChoiceField(
        choices=HAS_NEXT_ACTION_CHOICES,
        initial='no',
        required=False,
        widget=forms.RadioSelect,
        label='Is there a next action?',
    )
    followup_date = forms.DateTimeField(
        required=False,
        widget=DateTimeInput(attrs={'type': 'datetime-local', 'class': 'form-control'}),
        label='Next Follow-up Date',
    )
    followup_notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'What needs to be done next...'}),
        label='Next Action Details',
    )

    def __init__(self, *args, **kwargs):
        super(LeadActivityForm, self).__init__(*args, **kwargs)
        try:
            at_choices = ActivityType.get_choices()
            if at_choices:
                self.fields['activity_type'].choices = at_choices
        except Exception:
            pass
        try:
            na_choices = NextAction.get_choices()
            if na_choices:
                self.fields['next_action'] = forms.ChoiceField(
                    choices=[('', '— Select Next Action —')] + list(na_choices),
                    required=False,
                    widget=forms.Select(attrs={'class': 'form-control'}),
                )
        except Exception:
            pass

    class Meta:
        model = LeadActivity
        fields = [
            'activity_type', 'subject', 'description', 'outcome', 'next_action',
            'scheduled_date', 'duration', 'is_completed'
        ]
        widgets = {
            'scheduled_date': DateTimeInput(attrs={'type': 'datetime-local'}),
        }


class LeadAlternatePhoneForm(FormSettings):
    def __init__(self, *args, **kwargs):
        super(LeadAlternatePhoneForm, self).__init__(*args, **kwargs)

    class Meta:
        model = LeadAlternatePhone
        fields = ['phone', 'label']
        widgets = {
            'phone': TextInput(attrs={'placeholder': 'Alternate phone number'}),
            'label': TextInput(attrs={'placeholder': 'Relation (Father, Mother, etc.)'}),
        }


class BusinessForm(FormSettings):
    def __init__(self, *args, **kwargs):
        super(BusinessForm, self).__init__(*args, **kwargs)

    class Meta:
        model = Business
        fields = [
            'title', 'description', 'value', 'status', 'start_date', 'end_date',
            'payment_terms', 'notes'
        ]
        widgets = {
            'start_date': DateInput(attrs={'type': 'date'}),
            'end_date': DateInput(attrs={'type': 'date'}),
        }


class LeadTransferForm(FormSettings):
    def __init__(self, *args, **kwargs):
        super(LeadTransferForm, self).__init__(*args, **kwargs)

    class Meta:
        model = LeadTransfer
        fields = ['to_counsellor', 'reason']


class CounsellorEditForm(CustomUserForm):
    employee_id = forms.CharField(max_length=20, required=True)
    department = forms.CharField(max_length=100, required=False)
    is_active = forms.BooleanField(required=False)
    
    def __init__(self, *args, **kwargs):
        # Extract counsellor instance if provided
        counsellor_instance = kwargs.pop('counsellor_instance', None)
        super(CounsellorEditForm, self).__init__(*args, **kwargs)
        
        # If we have a counsellor instance, populate the counsellor-specific fields
        if counsellor_instance:
            self.fields['employee_id'].initial = counsellor_instance.employee_id
            self.fields['department'].initial = counsellor_instance.department
            self.fields['is_active'].initial = counsellor_instance.is_active

    class Meta(CustomUserForm.Meta):
        model = CustomUser
        fields = CustomUserForm.Meta.fields


class LeadImportForm(forms.Form):
    file = forms.FileField(
        label='Select a file',
        help_text='Upload Excel (.xlsx) or CSV (.csv) file',
        widget=forms.FileInput(attrs={'accept': '.xlsx,.csv'})
    )
    source = forms.ModelChoiceField(
        queryset=LeadSource.objects.filter(is_active=True),
        required=True,
        label='Lead Source'
    )
    assigned_counsellor = forms.ModelChoiceField(
        queryset=Counsellor.objects.filter(is_active=True),
        required=False,
        label='Assign to Counsellor (Optional)'
    )

    def clean_file(self):
        uploaded = self.cleaned_data.get('file')
        if not uploaded:
            return uploaded

        # Enforce extension whitelist
        ext = os.path.splitext(uploaded.name)[1].lower()
        allowed_exts = {'.csv', '.xlsx'}
        if ext not in allowed_exts:
            raise ValidationError("Unsupported file type. Only .csv and .xlsx files are allowed.")

        # Enforce MIME type where available
        content_type = getattr(uploaded, 'content_type', '') or ''
        allowed_types = {
            'text/csv',
            'application/vnd.ms-excel',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        }
        if content_type and content_type not in allowed_types:
            raise ValidationError("Invalid MIME type for lead import file.")

        # Enforce max size (server-side)
        max_size_mb = getattr(settings, 'MAX_LEAD_IMPORT_MB', 10)
        if uploaded.size > max_size_mb * 1024 * 1024:
            raise ValidationError(f"File too large. Max size is {max_size_mb}MB.")

        return uploaded


class NotificationCounsellorForm(FormSettings):
    def __init__(self, *args, **kwargs):
        super(NotificationCounsellorForm, self).__init__(*args, **kwargs)

    class Meta:
        model = NotificationCounsellor
        fields = ['counsellor', 'message']


class NotificationAdminForm(FormSettings):
    def __init__(self, *args, **kwargs):
        super(NotificationAdminForm, self).__init__(*args, **kwargs)

    class Meta:
        model = NotificationAdmin
        fields = ['admin', 'message']


class CounsellorPerformanceForm(FormSettings):
    def __init__(self, *args, **kwargs):
        super(CounsellorPerformanceForm, self).__init__(*args, **kwargs)

    class Meta:
        model = CounsellorPerformance
        fields = [
            'counsellor', 'month', 'total_leads_assigned', 'total_leads_contacted',
            'total_leads_qualified', 'total_business_generated', 'conversion_rate',
            'average_response_time'
        ]
        widgets = {
            'month': DateInput(attrs={'type': 'date'}),
        }


class DailyTargetForm(forms.Form):
    """Simple form: just a number + date + who to assign."""
    ASSIGN_MODE_CHOICES = [
        ('all', 'All Counsellors'),
        ('selected', 'Selected Counsellors'),
    ]

    target_count = forms.IntegerField(
        min_value=1, initial=100,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'e.g. 100'}),
        label='Number of tasks',
    )
    target_date = forms.DateField(
        widget=DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        label='Date',
    )
    assign_mode = forms.ChoiceField(
        choices=ASSIGN_MODE_CHOICES,
        widget=forms.RadioSelect,
        initial='all',
    )
    counsellors = forms.ModelMultipleChoiceField(
        queryset=Counsellor.objects.filter(is_active=True).select_related('admin'),
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )


class MetaIntegrationSettingsForm(forms.ModelForm):
    """Secrets: leave blank on save to keep the previous value."""

    class Meta:
        model = MetaIntegrationSettings
        fields = [
            "public_base_url",
            "verify_token",
            "app_secret",
            "access_token",
            "whatsapp_phone_number_id",
            "facebook_page_id",
            "whatsapp_enabled",
            "instagram_enabled",
            "facebook_messenger_enabled",
            "notify_admins_on_message",
        ]
        widgets = {
            "public_base_url": forms.URLInput(
                attrs={"class": "form-control", "placeholder": "https://your-domain.com"}
            ),
            "verify_token": forms.TextInput(attrs={"class": "form-control"}),
            "app_secret": forms.PasswordInput(
                render_value=False,
                attrs={"class": "form-control", "placeholder": "Leave blank to keep current"},
            ),
            "access_token": forms.PasswordInput(
                render_value=False,
                attrs={"class": "form-control", "placeholder": "Leave blank to keep current"},
            ),
            "whatsapp_phone_number_id": forms.TextInput(attrs={"class": "form-control"}),
            "facebook_page_id": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Numeric Page ID for Messenger/IG sends"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        inst = kwargs.get("instance")
        if inst:
            if inst.app_secret:
                self.fields["app_secret"].help_text = "Leave blank to keep the current secret."
            if inst.access_token:
                self.fields["access_token"].help_text = "Leave blank to keep the current token."

    def clean_public_base_url(self):
        url = (self.cleaned_data.get("public_base_url") or "").strip().rstrip("/")
        return url

    def save(self, commit=True):
        inst = super().save(commit=False)
        try:
            prev = MetaIntegrationSettings.objects.get(pk=1)
        except MetaIntegrationSettings.DoesNotExist:
            prev = None
        if prev:
            if not (self.cleaned_data.get("app_secret") or "").strip():
                inst.app_secret = prev.app_secret
            if not (self.cleaned_data.get("access_token") or "").strip():
                inst.access_token = prev.access_token
        if commit:
            inst.save()
        return inst

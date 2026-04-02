from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0022_proxy_settings_resilience_defaults"),
        ("core", "022_default_user_limit_settings"),
    ]

    operations = []

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("m3u", "0019_m3uaccountprofile_exp_date"),
    ]

    operations = [
        migrations.AlterField(
            model_name="m3uaccount",
            name="account_type",
            field=models.CharField(
                choices=[
                    ("STD", "Standard"),
                    ("XC", "Xtream Codes"),
                    ("STALKER", "Stalker"),
                ],
                default="STD",
                max_length=20,
            ),
        ),
    ]

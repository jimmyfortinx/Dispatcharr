from django.db import migrations


def update_proxy_settings_defaults(apps, schema_editor):
    CoreSettings = apps.get_model("core", "CoreSettings")

    defaults = {
        "buffering_timeout": 15,
        "buffering_speed": 1.0,
        "redis_chunk_ttl": 60,
        "channel_shutdown_delay": 0,
        "channel_init_grace_period": 5,
        "new_client_behind_seconds": 15,
        "connection_ready_chunks": 16,
        "max_reconnect_attempts": 5,
        "min_stable_time_before_reconnect": 10,
    }

    settings_obj, _ = CoreSettings.objects.get_or_create(
        key="proxy_settings",
        defaults={"name": "Proxy Settings", "value": defaults.copy()},
    )

    current = settings_obj.value if isinstance(settings_obj.value, dict) else {}
    updated = dict(current)

    if updated.get("new_client_behind_seconds") in (None, 5):
        updated["new_client_behind_seconds"] = 15
    if updated.get("connection_ready_chunks") in (None, 1):
        updated["connection_ready_chunks"] = 16
    if updated.get("max_reconnect_attempts") in (None, 3):
        updated["max_reconnect_attempts"] = 5
    if updated.get("min_stable_time_before_reconnect") in (None, 30):
        updated["min_stable_time_before_reconnect"] = 10

    if updated != current:
        settings_obj.value = updated
        settings_obj.save(update_fields=["value"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0021_systemnotification_notificationdismissal"),
    ]

    operations = [
        migrations.RunPython(update_proxy_settings_defaults, migrations.RunPython.noop),
    ]

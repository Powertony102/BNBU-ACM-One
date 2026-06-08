from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0007_event_checkin_managers'),
    ]

    operations = [
        migrations.AddField(
            model_name='eventqrcode',
            name='deactivated_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]

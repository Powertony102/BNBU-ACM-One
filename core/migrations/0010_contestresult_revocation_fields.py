from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0009_contest_rating_models'),
    ]

    operations = [
        migrations.AddField(
            model_name='contestresult',
            name='revoked_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='contestresult',
            name='revoked_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='revoked_contest_results', to='core.user'),
        ),
    ]

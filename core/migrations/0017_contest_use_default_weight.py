from decimal import Decimal

from django.db import migrations, models


DEFAULT_LEVEL_WEIGHT_MAP = {
    'national': Decimal('1.60'),
    'regional': Decimal('1.40'),
    'provincial': Decimal('1.20'),
    'campus': Decimal('1.00'),
    'internal': Decimal('0.80'),
}


def infer_weight_mode(apps, schema_editor):
    Contest = apps.get_model('core', 'Contest')
    for contest in Contest.objects.all():
        default_weight = DEFAULT_LEVEL_WEIGHT_MAP.get(contest.level, Decimal('1.00'))
        contest.use_default_weight = Decimal(str(contest.weight)) == default_weight
        contest.save(update_fields=['use_default_weight'])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0016_memberintegritysanction'),
    ]

    operations = [
        migrations.AddField(
            model_name='contest',
            name='use_default_weight',
            field=models.BooleanField(default=True),
        ),
        migrations.RunPython(infer_weight_mode, migrations.RunPython.noop),
    ]

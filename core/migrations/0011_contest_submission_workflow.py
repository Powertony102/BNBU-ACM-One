from decimal import Decimal

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0010_contestresult_revocation_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='contestteam',
            name='external_member_names',
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.CreateModel(
            name='ContestSubmission',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('contest_name', models.CharField(max_length=200)),
                ('contest_series', models.CharField(choices=[('icpc', 'ICPC'), ('ccpc', 'CCPC'), ('provincial', '省赛'), ('invitational', '邀请赛'), ('campus', '校赛'), ('selection', '选拔赛'), ('other', '其他')], default='other', max_length=20)),
                ('contest_season', models.CharField(blank=True, max_length=20)),
                ('contest_stage', models.CharField(blank=True, max_length=100)),
                ('contest_date', models.DateField()),
                ('organizer', models.CharField(blank=True, max_length=200)),
                ('contest_level', models.CharField(choices=[('national', '国家级'), ('regional', '区域级'), ('provincial', '省级'), ('campus', '校级'), ('internal', '队内')], default='campus', max_length=20)),
                ('team_name', models.CharField(max_length=200)),
                ('external_teammates', models.CharField(blank=True, max_length=255)),
                ('award_type', models.CharField(choices=[('gold', '金奖'), ('silver', '银奖'), ('bronze', '铜奖'), ('honorable', '优胜奖'), ('finalist', '入围'), ('participation', '参赛'), ('custom', '自定义')], default='participation', max_length=20)),
                ('award_label', models.CharField(blank=True, max_length=100)),
                ('rank_label', models.CharField(blank=True, max_length=100)),
                ('result_tier', models.CharField(choices=[('champion', '顶尖'), ('high', '高'), ('medium', '中'), ('entry', '入门')], default='entry', max_length=20)),
                ('evidence_url', models.URLField(blank=True)),
                ('submission_note', models.TextField(blank=True)),
                ('review_status', models.CharField(choices=[('pending', '待审核'), ('approved', '已通过'), ('rejected', '已驳回')], default='pending', max_length=20)),
                ('review_note', models.TextField(blank=True)),
                ('reviewed_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('applicant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='contest_submissions', to='core.user')),
                ('resolved_contest', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='submissions', to='core.contest')),
                ('resolved_result', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='submissions', to='core.contestresult')),
                ('resolved_team', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='submissions', to='core.contestteam')),
                ('reviewed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='reviewed_contest_submissions', to='core.user')),
                ('team_members', models.ManyToManyField(blank=True, related_name='contest_submissions', to='core.memberprofile')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
    ]

from decimal import Decimal

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0008_eventqrcode_deactivated_at'),
    ]

    operations = [
        migrations.CreateModel(
            name='Contest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200)),
                ('series', models.CharField(choices=[('icpc', 'ICPC'), ('ccpc', 'CCPC'), ('provincial', '省赛'), ('invitational', '邀请赛'), ('campus', '校赛'), ('selection', '选拔赛'), ('other', '其他')], default='other', max_length=20)),
                ('season', models.CharField(blank=True, max_length=20)),
                ('stage', models.CharField(blank=True, max_length=100)),
                ('contest_date', models.DateField()),
                ('organizer', models.CharField(blank=True, max_length=200)),
                ('level', models.CharField(choices=[('national', '国家级'), ('regional', '区域级'), ('provincial', '省级'), ('campus', '校级'), ('internal', '队内')], default='campus', max_length=20)),
                ('weight', models.DecimalField(decimal_places=2, default=Decimal('1.00'), max_digits=4)),
                ('status', models.CharField(choices=[('draft', '草稿'), ('published', '已发布'), ('archived', '已归档')], default='draft', max_length=20)),
                ('description', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='created_contests', to='core.user')),
            ],
            options={
                'ordering': ['-contest_date', '-id'],
            },
        ),
        migrations.CreateModel(
            name='ContestTeam',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('team_name', models.CharField(max_length=200)),
                ('coach_name', models.CharField(blank=True, max_length=100)),
                ('note', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('contest', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='teams', to='core.contest')),
                ('leader', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='led_contest_teams', to='core.memberprofile')),
                ('members', models.ManyToManyField(blank=True, related_name='contest_teams', to='core.memberprofile')),
            ],
            options={
                'ordering': ['contest__contest_date', 'team_name'],
            },
        ),
        migrations.CreateModel(
            name='ContestResult',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('award_type', models.CharField(choices=[('gold', '金奖'), ('silver', '银奖'), ('bronze', '铜奖'), ('honorable', '优胜奖'), ('finalist', '入围'), ('participation', '参赛'), ('custom', '自定义')], default='participation', max_length=20)),
                ('award_label', models.CharField(blank=True, max_length=100)),
                ('rank_label', models.CharField(blank=True, max_length=100)),
                ('result_tier', models.CharField(choices=[('champion', '顶尖'), ('high', '高'), ('medium', '中'), ('entry', '入门')], default='entry', max_length=20)),
                ('manual_bonus', models.IntegerField(default=0)),
                ('rating_delta', models.IntegerField(default=0)),
                ('verified', models.BooleanField(default=False)),
                ('verified_at', models.DateTimeField(blank=True, null=True)),
                ('evidence_url', models.URLField(blank=True)),
                ('note', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('contest', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='results', to='core.contest')),
                ('team', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='results', to='core.contestteam')),
                ('verified_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='verified_contest_results', to='core.user')),
            ],
            options={
                'ordering': ['-contest__contest_date', '-id'],
            },
        ),
        migrations.CreateModel(
            name='MemberCompetitionProfile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('current_rating', models.IntegerField(default=0)),
                ('current_level', models.CharField(default='unrated', max_length=30)),
                ('peak_rating', models.IntegerField(default=0)),
                ('peak_level', models.CharField(default='unrated', max_length=30)),
                ('primary_color', models.CharField(default='#7d8b99', max_length=20)),
                ('highest_award_label', models.CharField(blank=True, max_length=100)),
                ('last_calculated_at', models.DateTimeField(blank=True, null=True)),
                ('latest_contest_result', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='latest_for_members', to='core.contestresult')),
                ('member', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='competition_profile', to='core.memberprofile')),
            ],
            options={
                'ordering': ['-current_rating', 'member__real_name'],
            },
        ),
        migrations.AddConstraint(
            model_name='contestteam',
            constraint=models.UniqueConstraint(fields=('contest', 'team_name'), name='unique_team_name_per_contest'),
        ),
        migrations.AddConstraint(
            model_name='contestresult',
            constraint=models.UniqueConstraint(fields=('contest', 'team'), name='unique_team_result_per_contest'),
        ),
    ]

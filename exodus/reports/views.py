# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys

from django.conf import settings
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.db import connection
from django.db.models import Count
from django.http import Http404
from django.http import HttpResponse
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from minio import Minio

from exodus.core.dns import refresh_dns
from reports.models import Report, Application

# Workaround to avoid issue with DB migrations
if 'makemigrations' not in sys.argv and 'migrate' not in sys.argv:
    from .forms import TrackerForm


def _paginate(request, data):
    paginator = Paginator(data, settings.EX_PAGINATOR_COUNT)
    page = request.GET.get('page')

    try:
        paginated_data = paginator.page(page)
    except PageNotAnInteger:
        paginated_data = paginator.page(1)
    except EmptyPage:
        paginated_data = paginator.page(paginator.num_pages)

    return paginated_data


def get_reports(request, handle=None):
    title = ''
    filter = request.GET.get('filter', None)
    try:
        if filter == 'no_trackers':
            reports = Report.objects.filter(found_trackers=None).order_by('-creation_date')
            title = _('No known trackers')
        elif filter == 'most_trackers':
            reports = Report.objects.exclude(found_trackers=None).annotate(nb_trackers=Count('found_trackers')).order_by('-nb_trackers')
            title = _('Most trackers')
        else:
            reports = Report.objects.order_by('-creation_date')
            if handle:
                reports = reports.filter(application__handle=handle)
                title = handle
    except Report.DoesNotExist:
        raise Http404(_("reports do not exist"))

    reports_paged = _paginate(request, reports)
    return render(request, 'reports_list.html', {'reports': reports_paged, 'count': reports.count(), 'title': title})


def get_all_apps(request):
    try:
        apps_list = Application.objects.order_by('name', 'handle').distinct('name', 'handle')
    except Application.DoesNotExist:
        raise Http404(_("No apps found"))

    apps = _paginate(request, apps_list)
    return render(request, 'apps_list.html', {'apps': apps, 'count': apps_list.count()})


def detail(request, report_id):
    try:
        report = Report.objects.get(pk=report_id)
    except Report.DoesNotExist:
        raise Http404(_("report does not exist"))
    return render(request, 'report_details.html', {'report': report})


def refreshdns(request):
    if request.method == 'GET':
        refresh_dns.delay()
        return HttpResponse(status=200)


def get_app_icon(request, app_id):
    minioClient = Minio(
        settings.MINIO_URL,
        access_key=settings.MINIO_ACCESS_KEY,
        secret_key=settings.MINIO_SECRET_KEY,
        secure=settings.MINIO_SECURE
    )
    try:
        app = Application.objects.get(pk=app_id)
    except Application.DoesNotExist:
        raise Http404(_("App does not exist"))

    try:
        data = minioClient.get_object(settings.MINIO_BUCKET, app.icon_path)
        return HttpResponse(data.data, content_type="image/png")
    except Exception as err:
        print(err)
        with open(os.path.join(os.path.dirname(os.path.realpath(__file__)), 'android.jpeg'), "rb") as f:
            return HttpResponse(f.read(), content_type="image/jpeg")


def get_stats(request):
    from collections import namedtuple
    try:
        apps = Application.objects.order_by('name', 'handle').distinct('name', 'handle')
    except Exception as e:
        raise Http404(e)

    tracker_query = """
        SELECT tt.name, tt.id, COUNT(*) as count
        FROM reports_report_found_trackers AS ft, trackers_tracker AS tt
        WHERE tt.id = ft.tracker_id
        GROUP BY ft.tracker_id, tt.name, tt.id
        ORDER BY count
        DESC LIMIT 21;
    """
    cursor = connection.cursor()
    cursor.execute(tracker_query)
    desc = cursor.description
    nt_result = namedtuple('Result', [col[0] for col in desc])
    trackers = [nt_result(*row) for row in cursor.fetchall()]

    sum = len(apps)
    tracker_results = []
    for t in trackers:
        score = int(100.*t.count/sum)
        count = int(t.count)
        tracker_results.append({'id': t.id, 'name': t.name, 'score': score, 'count': count})

    return render(request, 'stats_details.html', {'trackers': tracker_results})


def by_tracker(request):
    if request.method == 'POST':
        form = TrackerForm(request.POST)
        if form.is_valid():
            trackers_id = form.cleaned_data.get('trackers')
            try:
                reports_list = Report.objects.order_by('-creation_date')
                for id in trackers_id:
                    reports_list = reports_list.filter(found_trackers=id)
            except Report.DoesNotExist:
                raise Http404("No reports found")

            return render(request, 'reports_by_tracker.html', {'reports': reports_list})

    else:
        form = TrackerForm()

    return render(request, 'search_trackers.html', {'form': form})

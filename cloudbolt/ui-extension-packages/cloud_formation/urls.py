from django.conf.urls import url
from xui.cloud_formation import views

xui_urlpatterns = [
    url(
        r"^cloud_formation/create/$",
        views.create_cft_blueprint,
        name="cloudformation_create",
    ),
    url(
        r"^cloud_formation/(?P<blueprint_id>\d+)/edit/$",
        views.edit_cft_blueprint,
        name="edit_cft_blueprint",
    ),
    url(
        r"^cloud_formation/(?P<blueprint_id>\d+)/delete/$",
        views.delete_cft_blueprint,
        name="delete_cft_blueprint",
    ),
    url(
        r"^cloud_formation/(?P<blueprint_id>\d+)/sync/$",
        views.sync_cft_blueprint,
        name="sync_cft_blueprint",
    ),
    url(
        r"^cloud_formation/conn_info/create_git_ci/$",
        views.create_connection_info,
        name="cft_ci_create",
    ),
    url(
        r"^cloud_formation/conn_info/(?P<ci_id>\d+)/edit/$",
        views.edit_connectioninfo,
        name="cft_ci_edit",
    ),
    url(
        r"^cloud_formation/conn_info/(?P<ci_id>\d+)/delete/$",
        views.delete_connectioninfo,
        name="cft_ci_delete",
    ),
]

from typing import Annotated, cast

from fastapi import Depends, Request
from temporalio.client import Client

from northdesk.settings import ApiSettings


def get_settings(request: Request) -> ApiSettings:
    return cast(ApiSettings, request.state.settings)


def get_temporal_client(request: Request) -> Client:
    return cast(Client, request.state.temporal_client)


SettingsDep = Annotated[ApiSettings, Depends(get_settings)]
TemporalClientDep = Annotated[Client, Depends(get_temporal_client)]

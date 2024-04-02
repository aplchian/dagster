import math
import random
from datetime import timedelta

import pendulum
from dagster import (
    AssetExecutionContext,
    MetadataValue,
    ObserveResult,
    ScheduleDefinition,
    TimestampMetadataValue,
    _check as check,
    asset,
    build_last_update_freshness_checks,
    build_sensor_for_freshness_checks,
    define_asset_job,
    observable_source_asset,
)


@observable_source_asset(group_name="freshness_checks")
def unreliable_source_events():
    """An asset which has a .5 probability of being updated every minute.

    We expect an update every 3 minutes, and the asset could have arrived within the last 2 minutes.
    """
    return ObserveResult(
        metadata={"dagster/last_updated_timestamp": get_last_updated_timestamp_unreliable_source()}
    )


@asset(non_argument_deps={unreliable_source_events.key}, group_name="freshness_checks")
def derived_asset(context: AssetExecutionContext):
    """An asset that depends on the unreliable source.

    We also expect this asset to be updated every 3 minutes.
    """
    latest_observations = context.instance.fetch_observations(
        records_filter=unreliable_source_events.key, limit=1
    )
    latest_updated_timestamp = check.float_param(
        check.not_none(latest_observations.records[0].asset_observation)
        .metadata["dagster/last_updated_timestamp"]
        .value,
        "latest_updated_timestamp",
    )
    if latest_updated_timestamp < pendulum.now().subtract(minutes=3).timestamp():
        raise Exception("source is stale, so I am going to fail :(")
    return 1


freshness_checks_unreliable_source = build_last_update_freshness_checks(
    assets=[unreliable_source_events],
    deadline_cron="*/4 * * * *",
    lower_bound_delta=timedelta(minutes=3),
)

freshness_checks_derived_asset = build_last_update_freshness_checks(
    assets=[derived_asset],
    deadline_cron="*/4 * * * *",
    lower_bound_delta=timedelta(minutes=3),
)

raw_events_schedule = ScheduleDefinition(
    job=define_asset_job("observe_raw_events", selection=[unreliable_source_events]),
    cron_schedule="*/3 * * * *",
)

derived_asset_schedule = ScheduleDefinition(
    job=define_asset_job("derived_asset_job", selection=[derived_asset]),
    cron_schedule="*/3 * * * *",
)

freshness_sensor = build_sensor_for_freshness_checks(
    freshness_checks=freshness_checks_derived_asset,
    minimum_interval_seconds=5,
)


def get_last_updated_timestamp_unreliable_source() -> TimestampMetadataValue:
    context = AssetExecutionContext.get()
    latest_observations = context.instance.fetch_observations(
        records_filter=context.asset_key, limit=1
    )

    if random.random() < 0.5 and len(latest_observations.records) > 1:
        return check.not_none(latest_observations.records[0].asset_observation).metadata[
            "dagster/last_updated_timestamp"
        ]  # type: ignore
    else:
        now = pendulum.now()
        rounded_minute = math.floor((now.minute - 1) / 3) * 3
        return MetadataValue.timestamp(now.replace(minute=rounded_minute))


def get_freshness_defs_pile():
    """Return all the relevant definitions which must be splatted into the repo."""
    return [
        unreliable_source_events,
        *freshness_checks_unreliable_source,
        raw_events_schedule,
        derived_asset,
        *freshness_checks_derived_asset,
        derived_asset_schedule,
        freshness_sensor,
    ]

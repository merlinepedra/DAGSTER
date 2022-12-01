from collections import defaultdict
from typing import List, Mapping, NamedTuple, Optional

from dagster import AssetKey, DagsterEventType, DagsterInstance, EventRecordsFilter
from dagster import _check as check
from dagster._core.definitions.asset_graph import AssetGraph
from dagster._core.definitions.multi_dimensional_partitions import (
    MultiPartitionKey,
    MultiPartitionsDefinition,
    get_multipartition_key_from_tags,
)
from dagster._core.definitions.partition import PartitionsDefinition, PartitionsSubset
from dagster._core.storage.tags import (
    MULTIDIMENSIONAL_PARTITION_PREFIX,
    get_dimension_from_partition_tag,
)
from dagster._serdes import deserialize_json_to_dagster_namedtuple, whitelist_for_serdes


@whitelist_for_serdes
class AssetStatusCacheValue(
    NamedTuple(
        "_AssetPartitionsStatusCacheValue",
        [
            ("latest_storage_id", int),
            ("partitions_def_id", Optional[str]),
            ("materialized_partition_subsets", Optional[str]),
            # Future partition subset features will go here, e.g. stale partition subsets
        ],
    )
):
    """
    Set of asset fields that reflect partition materialization status. This is used to display
    global partition status in the asset view.

    Properties:
        latest_storage_id (int): The latest evaluated storage id for the asset.
        partitions_def_id (Optional(str)): The serializable unique identifier for the partitions
            definition. When this value differs from the new partitions definition, this cache
            value needs to be recalculated. None if the asset is unpartitioned.
        materialized_partition_subsets (Optional(str)): The serializable representation of the
            materialized partition subsets, up to the latest storage id. None if the asset is
            unpartitioned.
    """

    def __new__(
        cls,
        latest_storage_id: int,
        partitions_def_id: Optional[str] = None,
        materialized_partition_subsets: Optional[str] = None,
    ):
        check.int_param(latest_storage_id, "latest_storage_id")
        check.opt_inst_param(
            materialized_partition_subsets,
            "materialized_partition_subsets",
            str,
        )
        check.opt_str_param(partitions_def_id, "partitions_def_id")
        return super(AssetStatusCacheValue, cls).__new__(
            cls, latest_storage_id, partitions_def_id, materialized_partition_subsets
        )

    @staticmethod
    def from_db_string(db_string):
        if not db_string:
            return None

        cached_data = deserialize_json_to_dagster_namedtuple(db_string)
        if not isinstance(cached_data, AssetStatusCacheValue):
            return None

        return cached_data

    def deserialize_materialized_partition_subsets(
        self, partitions_def: PartitionsDefinition
    ) -> PartitionsSubset:
        if not self.materialized_partition_subsets:
            return partitions_def.empty_subset()

        return partitions_def.deserialize_subset(self.materialized_partition_subsets)


def get_materialized_multipartitions_from_tags(
    instance: DagsterInstance, asset_key: AssetKey, partitions_def: MultiPartitionsDefinition
) -> List[MultiPartitionKey]:
    dimension_names = partitions_def.partition_dimension_names
    materialized_keys: List[MultiPartitionKey] = []
    for event_tags in instance.get_event_tags_for_asset(asset_key):
        event_partition_keys_by_dimension = {
            get_dimension_from_partition_tag(key): value
            for key, value in event_tags.items()
            if key.startswith(MULTIDIMENSIONAL_PARTITION_PREFIX)
        }

        if all(
            [
                dimension_name in event_partition_keys_by_dimension.keys()
                for dimension_name in dimension_names
            ]
        ):
            materialized_keys.append(
                MultiPartitionKey(
                    {
                        dimension_names[0]: event_partition_keys_by_dimension[dimension_names[0]],
                        dimension_names[1]: event_partition_keys_by_dimension[dimension_names[1]],
                    }
                )
            )
    return materialized_keys


def _rebuild_status_cache(
    instance: DagsterInstance,
    asset_key: AssetKey,
    latest_storage_id: int,
    partitions_def: Optional[PartitionsDefinition] = None,
):
    """
    This method refreshes the asset status cache for a given asset key. It recalculates
    the materialized partition subset for the asset key and updates the cache value.
    """
    if not partitions_def:
        return AssetStatusCacheValue(latest_storage_id=latest_storage_id)

    if isinstance(partitions_def, MultiPartitionsDefinition):
        materialized_keys = get_materialized_multipartitions_from_tags(
            instance, asset_key, partitions_def
        )
    else:
        materialized_keys = [
            key
            for key, count in instance.get_materialization_count_by_partition([asset_key])
            .get(asset_key, {})
            .items()
            if count > 0
        ]

    materialized_partition_subsets = partitions_def.empty_subset()
    partition_keys = partitions_def.get_partition_keys()
    materialized_keys = set(materialized_keys) & set(partition_keys)

    materialized_partition_subsets = materialized_partition_subsets.with_partition_keys(
        materialized_keys
    )

    return AssetStatusCacheValue(
        latest_storage_id=latest_storage_id,
        partitions_def_id=partitions_def.serializable_unique_identifier,
        materialized_partition_subsets=materialized_partition_subsets.serialize(),
    )


def _get_updated_status_cache(
    instance: DagsterInstance,
    asset_key: AssetKey,
    current_status_cache_value: AssetStatusCacheValue,
    partitions_def: Optional[PartitionsDefinition] = None,
):
    """
    This method accepts the current asset status cache value, and fetches unevaluated
    records from the event log. It then updates the cache value with the new materializations.
    """
    unevaluated_event_records = instance.get_event_records(
        event_records_filter=EventRecordsFilter(
            event_type=DagsterEventType.ASSET_MATERIALIZATION,
            asset_key=asset_key,
            after_cursor=current_status_cache_value.latest_storage_id
            if current_status_cache_value
            else None,
        )
    )

    if not unevaluated_event_records:
        return current_status_cache_value

    latest_storage_id = max([record.storage_id for record in unevaluated_event_records])
    if not partitions_def:
        return AssetStatusCacheValue(latest_storage_id=latest_storage_id)

    check.invariant(
        current_status_cache_value.partitions_def_id
        == partitions_def.serializable_unique_identifier
    )
    materialized_subset: PartitionsSubset = (
        partitions_def.deserialize_subset(current_status_cache_value.materialized_partition_subsets)
        if current_status_cache_value and current_status_cache_value.materialized_partition_subsets
        else partitions_def.empty_subset()
    )
    newly_materialized_partitions = set()

    for unevaluated_materializations in unevaluated_event_records:
        if not (
            unevaluated_materializations.event_log_entry.dagster_event
            and unevaluated_materializations.event_log_entry.dagster_event.is_step_materialization
        ):
            check.failed("Expected materialization event")
        newly_materialized_partitions.add(
            unevaluated_materializations.event_log_entry.dagster_event.partition
        )
    materialized_subset = materialized_subset.with_partition_keys(newly_materialized_partitions)
    return AssetStatusCacheValue(
        latest_storage_id=latest_storage_id,
        partitions_def_id=current_status_cache_value.partitions_def_id,
        materialized_partition_subsets=materialized_subset.serialize(),
    )


def _fetch_stored_asset_status_cache_values(
    instance: DagsterInstance, asset_key: Optional[AssetKey] = None
) -> Mapping[AssetKey, Optional[AssetStatusCacheValue]]:
    asset_records = (
        instance.get_asset_records()
        if not asset_key
        else instance.get_asset_records(asset_keys=[asset_key])
    )
    return {
        asset_record.asset_entry.asset_key: asset_record.asset_entry.cached_status
        for asset_record in asset_records
    }


def _get_fresh_asset_status_cache_values(
    instance: DagsterInstance,
    asset_graph: AssetGraph,
    asset_key: Optional[AssetKey] = None,  # If not provided, fetches all asset cache values
) -> Mapping[AssetKey, AssetStatusCacheValue]:
    cached_status_data_by_asset_key = _fetch_stored_asset_status_cache_values(instance, asset_key)

    updated_cache_values_by_asset_key = {}
    for asset_key, cached_status_data in cached_status_data_by_asset_key.items():
        if asset_key not in asset_graph.all_asset_keys:
            # Do not calculate new value if asset not in graph
            continue

        partitions_def = asset_graph.get_partitions_def(asset_key)
        if cached_status_data is None or cached_status_data.partitions_def_id != (
            partitions_def.serializable_unique_identifier if partitions_def else None
        ):
            event_records = instance.get_event_records(
                event_records_filter=EventRecordsFilter(
                    event_type=DagsterEventType.ASSET_MATERIALIZATION,
                    asset_key=asset_key,
                ),
                limit=1,
            )
            if event_records:
                updated_cache_values_by_asset_key[asset_key] = _rebuild_status_cache(
                    instance=instance,
                    asset_key=asset_key,
                    partitions_def=partitions_def,
                    latest_storage_id=next(iter(event_records)).storage_id,
                )
        else:
            updated_cache_values_by_asset_key[asset_key] = _get_updated_status_cache(
                instance=instance,
                asset_key=asset_key,
                partitions_def=partitions_def,
                current_status_cache_value=cached_status_data,
            )

    return updated_cache_values_by_asset_key


def update_asset_status_cache_values(
    instance: DagsterInstance, asset_graph: AssetGraph, asset_key: Optional[AssetKey] = None
) -> Mapping[AssetKey, AssetStatusCacheValue]:
    updated_cache_values_by_asset_key = _get_fresh_asset_status_cache_values(
        instance, asset_graph, asset_key
    )
    for asset_key, status_cache_value in updated_cache_values_by_asset_key.items():
        instance.update_asset_cached_status_data(asset_key, status_cache_value)

    return updated_cache_values_by_asset_key

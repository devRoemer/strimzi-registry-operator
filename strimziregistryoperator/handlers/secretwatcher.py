"""Kopf handler to react to changes to Strimzi-generated Kubernetes secrets
for the cluster CA certificate or the client certificates.
"""

__all__ = (
    "handle_secret_change",
    "refresh_with_new_cluster_ca",
    "refresh_with_new_client_secret",
)

import kopf

from .. import state
from ..certprocessor import create_secret
from ..deployments import update_deployment
from ..k8s import create_k8sclient, get_deployment, get_kafka_user, get_ssr
from ..strimzi import get_api_version


@kopf.on.event("", "v1", "secrets")
def handle_secret_change(
    spec, meta, namespace, name, uid, event, body, logger, **kwargs
):
    """Handle changes in secrets managed by Strimzi for the
    KafkaUser corresponding to a StrimziSchemaRegistry deployment.
    """
    # Act only on Secrets that have been created or updated
    if event["type"] not in ("ADDED", "MODIFIED"):
        return

    # Act only on Secrets with the matching strimzi.io/cluster configuration.
    try:
        if meta["labels"]["strimzi.io/cluster"] != state.cluster_name:
            return
    except KeyError:
        # didn't have a strimzio.io/cluster key; so no action is needed.
        return

    if name == f"{state.cluster_name}-cluster-ca-cert":
        # Handle a change in the cluster CA certificate
        refresh_with_new_cluster_ca(
            cluster_ca_secret=body, namespace=namespace, logger=logger
        )
    elif name in state.registry_names:
        # Handle a change in the KafkaUser client certificate of a
        # StrimziSchemaRegistry
        refresh_with_new_client_secret(
            kafkauser_secret=body, namespace=namespace, logger=logger
        )


def refresh_with_new_cluster_ca(*, cluster_ca_secret, namespace, logger):
    k8s_client = create_k8sclient()

    # Iterate over each managed registry...
    for registry_name in state.registry_names:
        cluster = cluster_ca_secret["metadata"]["labels"]["strimzi.io/cluster"]

        ssr_body = get_ssr(
            name=registry_name, namespace=namespace, k8s_client=k8s_client
        )

        ssr_spec = ssr_body["spec"]
        strimzi_api_version = get_api_version(
            registry_name=registry_name, spec=ssr_spec, logger=logger
        )

        kafkauser = get_kafka_user(
            namespace=namespace,
            name=registry_name,  # assume StrimziSchemaRegistry name matches
            k8s_client=k8s_client,
            strimzi_api_version=strimzi_api_version,
            raw=False,
        )

        kafka_user_secret_name = kafkauser["status"]["secret"]

        secret = create_secret(
            kafka_user_secret_name=kafka_user_secret_name,
            namespace=namespace,
            cluster=cluster,
            owner=ssr_body,
            k8s_client=k8s_client,
            cluster_ca_secret=cluster_ca_secret,
            logger=logger,
        )
        secret_version = secret["metadata"]["resourceVersion"]

        deployment = get_deployment(
            name=registry_name,
            namespace=namespace,
            k8s_client=k8s_client,
            raw=False,
        )

        update_deployment(
            deployment=deployment,
            secret_version=secret_version,
            name=registry_name,
            namespace=namespace,
            k8s_client=k8s_client,
        )


def refresh_with_new_client_secret(*, kafkauser_secret, namespace, logger):
    k8s_client = create_k8sclient()

    kafka_username = kafkauser_secret["metadata"]["name"]
    cluster = kafkauser_secret["metadata"]["labels"]["strimzi.io/cluster"]

    ssr_body = get_ssr(
        name=kafka_username, namespace=namespace, k8s_client=k8s_client
    )

    ssr_spec = ssr_body["spec"]
    strimzi_api_version = get_api_version(
        registry_name=kafka_username, spec=ssr_spec, logger=logger
    )

    kafkauser = get_kafka_user(
        namespace=namespace,
        name=kafka_username,  # assume StrimziSchemaRegistry name matches
        k8s_client=k8s_client,
        strimzi_api_version=strimzi_api_version,
        raw=False,
    )

    kafka_user_secret_name = kafkauser["status"]["secret"]

    secret = create_secret(
        kafka_user_secret_name=kafka_user_secret_name,
        namespace=namespace,
        cluster=cluster,
        owner=ssr_body,
        k8s_client=k8s_client,
        client_secret=kafkauser_secret,
        logger=logger,
    )
    secret_version = secret["metadata"]["resourceVersion"]

    deployment = get_deployment(
        name=kafka_username,
        namespace=namespace,
        k8s_client=k8s_client,
        raw=False,
    )

    update_deployment(
        deployment=deployment,
        secret_version=secret_version,
        name=kafka_username,
        namespace=namespace,
        k8s_client=k8s_client,
    )

"""Utilities for creating deployments and related resources."""

__all__ = ["get_kafka_bootstrap_server", "create_deployment", "create_service"]

from typing import Any, Dict, Mapping

import kopf


def get_kafka_bootstrap_server(kafka, *, listener_name):
    """Get the bootstrap server address for a Strimzi Kafka cluster
    corresponding to the named listener using information from the
    ``status.listeners`` field.

    Parameters
    ----------
    kafka : dict
        The Kafka resource.
    listener_name : str
        The name of the listener. In Strimzi `v1beta2`, this is
        `spec.listeners[].name`. In Strimzi `v1beta1`, this is
        `spec.listeners.[tls|plain|external]`.

    Returns
    -------
    server : str
        The bootstrap server connection info (``host:port``) for the given
        Kafka listener.
    """
    # Handle the legacy code path in a separate function
    if kafka["apiVersion"] == "kafka.strimzi.io/v1beta1":
        return _get_v1beta1_bootstrap_server(
            kafka, listener_type=listener_name
        )

    # This assumes kafka.strimzi.io/v1beta2 or later

    # As a fallback for some strimzi v1beta2 representations of
    # status.listeners, the status.listeners[].name field might be missing
    # so we need to use the status.listeners[].type field instead. First
    # look up the type corresponding the the named listener.
    listener_types = {
        listener["name"]: listener["type"]
        for listener in kafka["spec"]["kafka"]["listeners"]
    }
    if listener_name not in listener_types:
        raise kopf.TemporaryError(
            f"Listener named {listener_name} is not known. Available "
            f"listeners are {', '.join(listener_types.keys())}"
        )

    try:
        listeners = kafka["status"]["listeners"]
    except KeyError:
        raise kopf.TemporaryError(
            "Could not get status.listeners from Kafka resource.",
            delay=10,
        )

    for listener in listeners:
        try:
            # Current v1beta2 strimzi specs include a
            # status.listeners[].name field
            if "name" in listener and listener["name"] == listener_name:
                return _format_server_address(listener)

            # Otherwise use the easlier mapping of listener types to types
            # for the case when only status.listeners[].type is available.
            # There's potential degeneracy, but what can we do?
            elif listener["type"] == listener_types[listener_name]:
                return _format_server_address(listener)

        except (KeyError, IndexError):
            continue

    all_names = [listener.get("type") for listener in listeners]
    msg = (
        f"Could not find address of a listener named {listener_name} "
        f"from the Kafka resource. Available names: {', '.join(all_names)}"
    )
    raise kopf.Error(msg, delay=10)


def _format_server_address(listener_status: dict) -> str:
    # newer versions of Strimzi provide a status.listeners[].bootstrapServers
    # field, but we can compute that from
    # status.listeners[].addresses[0] as a fallback
    if "bootstrapServers" in listener_status.keys():
        return listener_status["bootstrapServers"]
    else:
        address = listener_status["addresses"][0]
        return f'{address["host"]}:{address["port"]}'


def _get_v1beta1_bootstrap_server(
    kafka: Mapping, *, listener_type: str
) -> str:
    try:
        listeners_status = kafka["status"]["listeners"]
    except KeyError:
        raise kopf.TemporaryError(
            "Could not get status.listeners from Kafka resource.",
            delay=10,
        )

    for listener_status in listeners_status:
        try:
            if listener_status["type"] == listener_type:
                # build boostrap server connection info
                return _format_server_address(listener_status)
        except (KeyError, IndexError):
            continue

    all_listener_types = [
        listener.get("type", "UNKNOWN") for listener in listeners_status
    ]
    raise kopf.TemporaryError(
        f"Could not find address of a {listener_type} listener"
        f"from the Kafka resource. Available types: {all_listener_types}",
        delay=10,
    )


def create_deployment(*, name, bootstrap_server, secret_name, secret_version):
    """Create the JSON resource for a Deployment of the Confluence Schema
    Registry.

    Parameters
    ----------
    name : `str`
        Name of the StrimziKafkaUser, which is also used as the name of the
        deployment.
    bootstrap_server : `str`
        The ``host:port`` of the Kafka bootstrap service. See
        `get_cluster_tls_listener`.
    secret_name : `str`
        Name of the Secret resource containing the JKS-formatted keystore
        and truststore.
    secret_version : `str`
        The ``resourceVersion`` of the Secret containing the JKS-formatted
        keystore and truststore.

    Returns
    -------
    deployment : `dict`
        The Deployment resource.
    """
    key_prefix = "strimziregistryoperator.roundtable.lsst.codes"

    registry_container = create_container_spec(
        secret_name=secret_name, bootstrap_server=bootstrap_server
    )

    # The pod template
    template = {
        "metadata": {
            "labels": {"app": name},
            "annotations": {f"{key_prefix}/jksVersion": secret_version},
        },
        "spec": {
            "containers": [registry_container],
            "volumes": [
                {"name": "tls", "secret": {"secretName": secret_name}}
            ],
        },
    }

    dep = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": name, "labels": {"app": name}},
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": {"app": name}},
            "template": template,
        },
    }

    return dep


def create_container_spec(*, secret_name, bootstrap_server):
    """Create the container spec for the Schema Registry deployment."""
    registry_env = [
        {
            "name": "SCHEMA_REGISTRY_HOST_NAME",
            "valueFrom": {"fieldRef": {"fieldPath": "status.podIP"}},
        },
        {"name": "SCHEMA_REGISTRY_LISTENERS", "value": "http://0.0.0.0:8081"},
        {
            "name": "SCHEMA_REGISTRY_KAFKASTORE_BOOTSTRAP_SERVERS",
            "value": bootstrap_server,
        },
        # NOTE: This can likely be left to the default
        # {
        #     'name': 'SCHEMA_REGISTRY_KAFKASTORE_GROUP_ID',
        #     'value': None,  # FIXME
        # },
        {
            "name": "SCHEMA_REGISTRY_AVRO_COMPATIBILITY_LEVEL",
            "value": "forward",
        },
        {"name": "SCHEMA_REGISTRY_MASTER_ELIGIBILITY", "value": "true"},
        {
            "name": "SCHEMA_REGISTRY_HEAP_OPTS",
            "value": "-Xms512M -Xmx512M",
        },
        {
            "name": "SCHEMA_REGISTRY_KAFKASTORE_TOPIC",
            "value": "registry-schemas",
        },
        {
            "name": "SCHEMA_REGISTRY_KAFKASTORE_SSL_KEYSTORE_LOCATION",
            "value": "/var/schemaregistry/keystore.jks",
        },
        {
            "name": "SCHEMA_REGISTRY_KAFKASTORE_SSL_KEYSTORE_PASSWORD",
            "valueFrom": {
                "secretKeyRef": {
                    "name": secret_name,
                    "key": "keystore_password",
                }
            },
        },
        {
            "name": "SCHEMA_REGISTRY_KAFKASTORE_SSL_TRUSTSTORE_LOCATION",
            "value": "/var/schemaregistry/truststore.jks",
        },
        {
            "name": "SCHEMA_REGISTRY_KAFKASTORE_SSL_TRUSTSTORE_PASSWORD",
            "valueFrom": {
                "secretKeyRef": {
                    "name": secret_name,
                    "key": "truststore_password",
                }
            },
        },
        {
            "name": "SCHEMA_REGISTRY_KAFKASTORE_SECURITY_PROTOCOL",
            "value": "SSL",
        },
    ]

    registry_container = {
        "name": "server",
        "image": "confluentinc/cp-schema-registry:5.3.1",
        "imagePullPolicy": "IfNotPresent",
        "ports": [
            {
                "name": "schema-registry",
                "containerPort": 8081,
                "protocol": "TCP",
            }
        ],
        "env": registry_env,
        "volumeMounts": [
            {
                "mountPath": "/var/schemaregistry",
                "name": "tls",
                "readOnly": True,
            }
        ],
    }

    return registry_container


def create_service(
    *, name: str, service_type: str = "ClusterIp"
) -> Dict[str, Any]:
    """Create a Service resource for the Schema Registry.

    Parameters
    ----------
    name : `str`
        Name of the StrimziKafkaUser, which is also used as the name of the
        deployment.
    service_type : `str`
        The Kubernetes service type. Typically ClusterIP, but could be
        NodePort for testing with Minikube.

    Returns
    -------
    service : `dict`
        The Service resource.
    """
    s = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": name, "labels": {"name": name}},
        "spec": {
            "type": service_type,
            "ports": [{"name": "schema-registry", "port": 8081}],
            "selector": {
                "app": name,
            },
        },
    }

    return s


def update_deployment(
    *, deployment, secret_version, k8s_client, name, namespace
):
    """Update the schema registry deploymeent with a new Secret version
    to trigger a refresh of all its pods.
    """
    key_prefix = "strimziregistryoperator.roundtable.lsst.codes"
    secret_version_key = f"{key_prefix}/jksVersion"
    deployment.metadata.annotations[secret_version_key] = secret_version

    apps_api = k8s_client.AppsV1Api()
    apps_api.patch_namespaced_deployment(
        name=name, namespace=namespace, body=deployment
    )

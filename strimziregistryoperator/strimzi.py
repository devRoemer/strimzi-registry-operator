__all__ = ("get_api_version", "get_listener")


def get_api_version(registry_name, spec, logger):
    """Get the strimzi api version

    Parameters
    ----------
    spec : dict
        The ``spec`` field of the ``StrimziSchemaRegistry`` custom Kubernetes
        resource.

    Returns
    -------
    version
        The version string of the strimzi api
    """
    try:
        strimzi_api_version = spec["strimziVersion"]
    except KeyError:
        try:
            strimzi_api_version = spec["strimzi-version"]
            logger.warning(
                "The strimzi-version configuration is deprecated. "
                "Use strimziVersion instead."
            )
        except KeyError:
            strimzi_api_version = "v1beta2"
            logger.warning(
                "StrimziSchemaRegistry %s is missing a strimziVersion, "
                "using default %s",
                registry_name,
                strimzi_api_version,
            )


def get_listener(registry_name, spec, logger):
    """Get the listener

    Parameters
    ----------
    spec : dict
        The ``spec`` field of the ``StrimziSchemaRegistry`` custom Kubernetes
        resource.

    Returns
    -------
    listener
        The listener name
    """
    try:
        listener_name = spec["listener"]
    except KeyError:
        listener_name = "tls"
        logger.warning(
            "StrimziSchemaRegistry %s is missing a listener name, "
            "using default %s",
            registry_name,
            listener_name,
        )

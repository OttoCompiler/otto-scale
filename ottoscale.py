#!/usr/bin/env python3

"""

docker autoscaler flask app

provides endpoints to automatically scale Docker containers up or down.

"""

from flask import Flask, jsonify, request
import docker
import os
import logging
from datetime import datetime


app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


CONTAINER_IMAGE = os.getenv('CONTAINER_IMAGE', 'nginx:alpine')
CONTAINER_PREFIX = os.getenv('CONTAINER_PREFIX', 'scaled_app')
MIN_CONTAINERS = int(os.getenv('MIN_CONTAINERS', '1'))
MAX_CONTAINERS = int(os.getenv('MAX_CONTAINERS', '10'))


try:
    docker_client = docker.from_env()
    logger.info("Docker client initialized successfully")
except Exception as e:
    logger.error(f"Failed to initialize Docker client: {e}")
    docker_client = None


def get_managed_containers():
    if not docker_client:
        return []
    try:
        all_containers = docker_client.containers.list(all=True)
        return [c for c in all_containers if c.name.startswith(CONTAINER_PREFIX)]
    except Exception as e:
        logger.error(f"Error getting containers: {e}")
        return []


def get_running_count():
    containers = get_managed_containers()
    return len([c for c in containers if c.status == 'running'])


def create_container():
    if not docker_client:
        raise Exception("Docker client not available")

    count = len(get_managed_containers())
    container_name = f"{CONTAINER_PREFIX}_{count + 1}_{int(datetime.now().timestamp())}"

    try:
        container = docker_client.containers.run(
            CONTAINER_IMAGE,
            name=container_name,
            detach=True,
            remove=False,
            labels={"managed_by": "auto_scaler"}
        )
        logger.info(f"Created container: {container_name}")
        return container
    except Exception as e:
        logger.error(f"Failed to create container: {e}")
        raise


def remove_container():
    if not docker_client:
        raise Exception("Docker client not available")
    containers = [c for c in get_managed_containers() if c.status == 'running']
    if not containers:
        raise Exception("No running containers to remove")
    container = containers[-1]  # Remove the most recent one
    try:
        container.stop(timeout=10)
        container.remove()
        logger.info(f"Removed container: {container.name}")
        return container.name
    except Exception as e:
        logger.error(f"Failed to remove container: {e}")
        raise


@app.route('/health', methods=['GET'])
def health():
    docker_status = "connected" if docker_client else "disconnected"
    return jsonify({
        "status": "healthy",
        "docker": docker_status,
        "timestamp": datetime.now().isoformat()
    })


@app.route('/scale/up', methods=['POST'])
def scale_up():
    try:
        current = get_running_count()
        if current >= MAX_CONTAINERS:
            return jsonify({
                "success": False,
                "message": f"Maximum container limit reached ({MAX_CONTAINERS})",
                "current_count": current
            }), 400
        container = create_container()
        new_count = get_running_count()
        return jsonify({
            "success": True,
            "message": "Scaled up successfully",
            "container_name": container.name,
            "previous_count": current,
            "current_count": new_count
        })
    except Exception as e:
        logger.error(f"Scale up failed: {e}")
        return jsonify({
            "success": False,
            "message": str(e)
        }), 500


@app.route('/scale/down', methods=['POST'])
def scale_down():
    try:
        current = get_running_count()
        if current <= MIN_CONTAINERS:
            return jsonify({
                "success": False,
                "message": f"Minimum container limit reached ({MIN_CONTAINERS})",
                "current_count": current
            }), 400
        container_name = remove_container()
        new_count = get_running_count()
        return jsonify({
            "success": True,
            "message": "Scaled down successfully",
            "removed_container": container_name,
            "previous_count": current,
            "current_count": new_count
        })
    except Exception as e:
        logger.error(f"Scale down failed: {e}")
        return jsonify({
            "success": False,
            "message": str(e)
        }), 500


@app.route('/scale/set', methods=['POST'])
def scale_set():
    try:
        data = request.get_json()
        target_count = data.get('count')
        if target_count is None:
            return jsonify({
                "success": False,
                "message": "Missing 'count' parameter"
            }), 400
        target_count = int(target_count)
        if target_count < MIN_CONTAINERS or target_count > MAX_CONTAINERS:
            return jsonify({
                "success": False,
                "message": f"Count must be between {MIN_CONTAINERS} and {MAX_CONTAINERS}"
            }), 400
        current = get_running_count()
        diff = target_count - current

        if diff > 0:
            for _ in range(diff):
                create_container()
        elif diff < 0:
            for _ in range(abs(diff)):
                remove_container()
        new_count = get_running_count()
        return jsonify({
            "success": True,
            "message": f"Scaled to {target_count} containers",
            "previous_count": current,
            "current_count": new_count
        })
    except Exception as e:
        logger.error(f"Scale set failed: {e}")
        return jsonify({
            "success": False,
            "message": str(e)
        }), 500


@app.route('/status', methods=['GET'])
def status():
    try:
        containers = get_managed_containers()
        running = [c for c in containers if c.status == 'running']
        container_details = [{
            "name": c.name,
            "status": c.status,
            "image": c.image.tags[0] if c.image.tags else "unknown",
            "created": c.attrs['Created']
        } for c in containers]

        return jsonify({
            "total_containers": len(containers),
            "running_containers": len(running),
            "min_containers": MIN_CONTAINERS,
            "max_containers": MAX_CONTAINERS,
            "container_image": CONTAINER_IMAGE,
            "containers": container_details
        })
    except Exception as e:
        logger.error(f"Status check failed: {e}")
        return jsonify({
            "success": False,
            "message": str(e)
        }), 500


if __name__ == '__main__':
    try:
        current = get_running_count()
        if current < MIN_CONTAINERS:
            logger.info(f"Starting {MIN_CONTAINERS - current} initial containers")
            for _ in range(MIN_CONTAINERS - current):
                create_container()
    except Exception as e:
        logger.warning(f"Failed to start initial containers: {e}")

    app.run(host='0.0.0.0', port=5027, debug=True)

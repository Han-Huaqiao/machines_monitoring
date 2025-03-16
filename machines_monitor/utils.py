import yaml
from typing import Optional, Dict, Any


def read_yaml_file(file_path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(file_path, 'r') as file:
            config = yaml.safe_load(file)
            return config
    except Exception as e:
        raise RuntimeError(f"Error reading YAML file: {e}.")

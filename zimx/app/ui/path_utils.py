"""Utilities for converting between filesystem paths and colon notation."""
from __future__ import annotations

from pathlib import Path
from zimx.server.adapters.files import PAGE_SUFFIX


def path_to_colon(file_path: str) -> str:
    """Convert a filesystem path like /PageA/PageB/PageC/PageC.txt to PageA:PageB:PageC.
    
    The structure is: Each page lives in a folder with the same name.
    /JoeBob/JoeBob2/JoeBob2.txt should display as JoeBob:JoeBob2
    
    Args:
        file_path: Vault-relative path starting with / (e.g., "/PageA/PageB/PageC/PageC.txt")
        
    Returns:
        Colon-separated page hierarchy (e.g., "PageA:PageB:PageC")
    """
    # Strip whitespace first, then strip slashes
    cleaned = file_path.strip().strip("/")
    if not cleaned:
        return ""
    
    parts = cleaned.split("/")
    # Remove .txt suffix from last part if present
    if parts and parts[-1].endswith(PAGE_SUFFIX):
        parts[-1] = parts[-1][:-len(PAGE_SUFFIX)]
    
    # The filesystem structure is /Folder1/Folder2/Folder2.txt
    # The last part (file name) matches the second-to-last (folder name)
    # We want to display this as Folder1:Folder2, not Folder1:Folder2:Folder2
    if len(parts) >= 2 and parts[-1] == parts[-2]:
        # Remove the duplicate file name
        parts = parts[:-1]
    
    return ":".join(parts)


def colon_to_path(colon_path: str, vault_root_name: str = "") -> str:
    """Convert colon notation like PageA:PageB:PageC to filesystem path.
    
    The structure is: Each page lives in a folder with the same name.
    PageA:PageB:PageC becomes /PageA/PageB/PageC/PageC.txt
    
    Args:
        colon_path: Colon-separated page hierarchy (e.g., "PageA:PageB:PageC")
        vault_root_name: Name of the vault root (optional, for handling root page)
        
    Returns:
        Vault-relative filesystem path (e.g., "/PageA/PageB/PageC/PageC.txt")
    """
    if not colon_path:
        if vault_root_name:
            return f"/{vault_root_name}{PAGE_SUFFIX}"
        return "/"
    
    parts = colon_path.split(":")
    # Each page lives in a folder with the same name
    # Final path is /Part1/Part2/.../PartN/PartN.txt
    folder_path = "/".join(parts)
    file_name = f"{parts[-1]}{PAGE_SUFFIX}"
    return f"/{folder_path}/{file_name}"


def colon_to_folder_path(colon_path: str) -> str:
    """Convert colon notation to folder path (without the .txt file).
    
    Args:
        colon_path: Colon-separated page hierarchy (e.g., "PageA:PageB:PageC")
        
    Returns:
        Folder path (e.g., "/PageA/PageB/PageC")
    """
    if not colon_path:
        return "/"
    
    parts = colon_path.split(":")
    return "/" + "/".join(parts)

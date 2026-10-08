"""Dedicated desktop workspace host services."""
from termx.workspace.http import mount_workspace
from termx.workspace.service import WorkspaceService

__all__ = ['WorkspaceService','mount_workspace']

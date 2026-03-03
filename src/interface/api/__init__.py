"""FastAPI web interface for Narada.

Provides REST API endpoints for playlist management, connector status,
and future workflow execution. Sits in the interface layer alongside CLI —
both call the same use cases through execute_use_case().
"""

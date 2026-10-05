"""OS-specific integrations, one subpackage per platform.

Everything here is imported lazily by the code that picks a platform, so a
module for one OS is never loaded on another.
"""

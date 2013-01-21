#
#    Copyright (c) 2013 Tom Keffer <tkeffer@gmail.com>
#
#    See the file LICENSE.txt for your full rights.
#
"""Weepwr package."""

#===============================================================================
#                        Exception Classes
#===============================================================================

class WeePwrIOError(IOError):
    """Exception raised for I/O errors."""

class EndError(WeePwrIOError):
    """Exception raised for a bad header, footer, or packet type"""
    
class CheckSumError(WeePwrIOError):
    """Exception raised in the event of a checksum error."""
    
class ResponseError(WeePwrIOError):
    """Exception raised when the GEM does not respond as expected."""

class CounterResetError(WeePwrIOError):
    """Exception when a counter is reset."""
    

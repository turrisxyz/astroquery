# Licensed under a 3-clause BSD style license - see LICENSE.rst
"""
CDMS catalog
------------
Cologne Database for Molecular Spectroscopy


:author: Giannina Guzman (gguzman2@villanova.edu)
:author: Miguel de Val-Borro (miguel.deval@gmail.com)
:author: Adam Ginsburg (adam.g.ginsburg@gmail.com)

"""
from astropy import config as _config


class Conf(_config.ConfigNamespace):
    """
    Configuration parameters for `astroquery.cdms`.
    """
    server = _config.ConfigItem(
        'https://cdms.astro.uni-koeln.de/cgi-bin/cdmssearch',
        'CDMS Search and Conversion Form URL.')

    timeout = _config.ConfigItem(
        60,
        'Time limit for connecting to the CDMS server.')


conf = Conf()

from .core import CDMS, CDMSClass

__all__ = ['CDMS', 'CDMSClass',
           'Conf', 'conf',
           ]

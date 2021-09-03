# Licensed under a 3-clause BSD style license - see LICENSE.rst
import numpy as np
import os
import warnings

from bs4 import BeautifulSoup
import astropy.units as u
from astropy.io import ascii
from ..query import BaseQuery
from ..utils import async_to_sync
# import configurable items declared in __init__.py
from . import conf
from ..jplspec import lookup_table


__all__ = ['CDMS', 'CDMSClass']


def data_path(filename):
    data_dir = os.path.join(os.path.dirname(__file__), 'data')
    return os.path.join(data_dir, filename)


@async_to_sync
class CDMSClass(BaseQuery):
    """
    """

    # use the Configuration Items imported from __init__.py
    URL = conf.server
    TIMEOUT = conf.timeout

    def query_lines_async(self, min_frequency, max_frequency,
                          min_strength=-500, molecule='All',
                          temperature_for_intensity=300, flags=0,
                          parse_name_locally=False, get_query_payload=False,
                          cache=True):
        """
        Creates an HTTP POST request based on the desired parameters and
        returns a response.

        Parameters
        ----------
        min_frequency : `astropy.units`
            Minimum frequency (or any spectral() equivalent)
        max_frequency : `astropy.units`
            Maximum frequency (or any spectral() equivalent)
        min_strength : int, optional
            Minimum strength in catalog units, the default is -500

        molecule : list, string of regex if parse_name_locally=True, optional
            Identifiers of the molecules to search for. If this parameter
            is not provided the search will match any species. Default is 'All'.

        temperature_for_intensity : float
            The temperature to use when computing the intensity Smu^2.  Set
            to 300 by default for compatibility with JPL and the native
            catalog format, which defaults to 300.
            ** If temperature is set to zero, the return value in this column
            will be the Einstein A value **

        flags : int, optional
            Regular expression flags. Default is set to 0

        parse_name_locally : bool, optional
            When set to True it allows the method to parse through catdir.cat
            in order to match the regex inputted in the molecule parameter
            and request the corresponding tags of the matches instead. Default
            is set to False

        get_query_payload : bool, optional
            When set to `True` the method should return the HTTP request
            parameters as a dict. Default value is set to False

        Returns
        -------
        response : `requests.Response`
            The HTTP response returned from the service.

        Examples
        --------
        >>> table = CDMS.query_lines(min_frequency=100*u.GHz,
        ...                             max_frequency=110*u.GHz,
        ...                             min_strength=-500,
        ...                             molecule="018505 H2O+") # doctest: +REMOTE_DATA
        >>> print(table) # doctest: +SKIP
            FREQ     ERR   LGINT   DR   ELO    GUP  TAG  QNFMT  Ju  Ku  vu  Jl  Kl  vl      F      name
            MHz      MHz  MHz nm2      1 / cm
        ----------- ----- ------- --- -------- --- ----- ----- --- --- --- --- --- --- ----------- ----
        103614.4941 2.237 -4.1826   3 202.8941   8 18505  2356   4   1   4   4   0   4 3 2 1 3 0 3 H2O+
        107814.8763 148.6 -5.4438   3 878.1191  12 18505  2356   6   5   1   7   1   6 7 4 4 8 1 7 H2O+
        107822.3481 148.6 -5.3846   3 878.1178  14 18505  2356   6   5   1   7   1   7 7 4 4 8 1 8 H2O+
        107830.1216 148.6 -5.3256   3 878.1164  16 18505  2356   6   5   1   7   1   8 7 4 4 8 1 9 H2O+
        """
        # first initialize the dictionary of HTTP request parameters
        payload = dict()

        if min_frequency is not None and max_frequency is not None:
            # allow setting payload without having *ANY* valid frequencies set
            min_frequency = min_frequency.to(u.GHz, u.spectral())
            max_frequency = max_frequency.to(u.GHz, u.spectral())
            if min_frequency > max_frequency:
                min_frequency, max_frequency = max_frequency, min_frequency

            payload['MinNu'] = min_frequency.value
            payload['MaxNu'] = max_frequency.value

        payload['UnitNu'] = 'GHz'
        payload['StrLim'] = min_strength
        payload['temp'] = temperature_for_intensity
        payload['logscale'] = 'yes'
        payload['mol_sort_query'] = 'tag'
        payload['sort'] = 'frequency'
        payload['output'] = 'text'
        payload['but_action'] = 'Submit'

        # changes interpretation of query
        self._last_query_temperature = temperature_for_intensity

        if molecule is not None:
            if parse_name_locally:
                self.lookup_ids = build_lookup()
                luts = self.lookup_ids.find(molecule, flags)
                payload['Molecules'] = tuple(f"{val:06d} {key}"
                                             for key, val in luts.items())[0]
                if len(molecule) == 0:
                    raise ValueError('No matching species found. Please\
                                     refine your search or read the Docs\
                                     for pointers on how to search.')
            else:
                payload['Molecules'] = molecule

        payload = list(payload.items())

        if get_query_payload:
            return payload
        # BaseQuery classes come with a _request method that includes a
        # built-in caching system
        response = self._request(method='POST', url=self.URL, data=payload,
                                 timeout=self.TIMEOUT, cache=cache)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')

        ok = False
        urls = [x.attrs['src'] for x in soup.findAll('frame',)]
        for url in urls:
            if 'tab' in url and 'head' not in url:
                ok = True
                break
        if not ok:
            raise ValueError("Did not find table in response")

        baseurl = self.URL.split('cgi-bin')[0]
        fullurl = f'{baseurl}/{url}'

        response2 = self._request(method='GET', url=fullurl,
                                  timeout=self.TIMEOUT, cache=cache)

        return response2

    def _parse_result(self, response, verbose=False):
        """
        Parse a response into an `~astropy.table.Table`

        The catalog data files are composed of fixed-width card images, with
        one card image per spectral line.  The format of each card image is
        similar to the JPL version:
        FREQ, ERR, LGINT, DR,  ELO, GUP, TAG, QNFMT,  QN',  QN"
        (F13.4,F8.4, F8.4,  I2,F10.4,  I3,  I7,    I4,  6I2,  6I2)
        but the formats are somewhat different and are encoded below.
        The first several entries are the same, but more detail is appended at
        the end of the line

        FREQ:  Frequency of the line in MHz.
        ERR:   Estimated or experimental error of FREQ in MHz.
        LGINT: Base 10 logarithm of the integrated intensity in units of nm^2 MHz at
            300 K.

        DR:    Degrees of freedom in the rotational partition function (0 for atoms,
            2 for linear molecules, and 3 for nonlinear molecules).

        ELO:   Lower state energy in cm^{-1} relative to the ground state.
        GUP:   Upper state degeneracy.
        TAG:   Species tag or molecular identifier.
            A negative value flags that the line frequency has
            been measured in the laboratory.  The absolute value of TAG is then the
            species tag and ERR is the reported experimental error.  The three most
            significant digits of the species tag are coded as the mass number of
            the species.

        QNFMT: Identifies the format of the quantum numbers
        Ju/Ku/vu and Jl/Kl/vl are the upper/lower QNs
        F: the hyperfine lines
        name: molecule name

        The full detailed description is here:
        https://cdms.astro.uni-koeln.de/classic/predictions/description.html#description
        """

        if 'Zero lines were found' in response.text:
            raise ValueError(f"Response was empty; message was '{response.text}'.")

        soup = BeautifulSoup(response.text, 'html.parser')
        text = soup.find('pre').text

        starts = {'FREQ': 0,
                  'ERR': 14,
                  'LGINT': 25,
                  'DR': 36,
                  'ELO': 38,
                  'GUP': 48,
                  'TAG': 51,
                  'QNFMT': 57,
                  'Ju': 61,
                  'Ku': 63,
                  'vu': 65,
                  'Jl': 67,
                  'Kl': 69,
                  'vl': 71,
                  'F': 73,
                  'name': 89}

        result = ascii.read(text, header_start=None, data_start=0,
                            comment=r'THIS|^\s{12,14}\d{4,6}.*',
                            names=list(starts.keys()),
                            col_starts=list(starts.values()),
                            format='fixed_width', fast_reader=False)

        result['FREQ'].unit = u.MHz
        result['ERR'].unit = u.MHz

        # if there is a crash at this step, something went wrong with the query
        # and the _last_query_temperature was not set.  This shouldn't ever
        # happen, but, well, I anticipate it will.
        if self._last_query_temperature == 0:
            result.rename_column('LGINT', 'LGAIJ')
            result['LGAIJ'].unit = u.s**-1
        else:
            result['LGINT'].unit = u.nm**2 * u.MHz
        result['ELO'].unit = u.cm**(-1)

        return result

    def get_species_table(self, catfile='catdir.cat'):
        """
        A directory of the catalog is found in a file called 'catdir.cat.'

        The table is derived from https://cdms.astro.uni-koeln.de/classic/entries/partition_function.html

        Parameters
        -----------
        catfile : str, name of file, default 'catdir.cat'
            The catalog file, installed locally along with the package

        Returns
        --------
        Table: `~astropy.table.Table`
            | tag : The species tag or molecular identifier.
            | molecule : An ASCII name for the species.
            | #line : The number of lines in the catalog.
            | lg(Q(n)) : A seven-element vector containing the base 10 logarithm of
                the partition function.

        """

        result = ascii.read(data_path('catdir.cat'), format='csv',
                            delimiter='|')

        meta = {'lg(Q(1000))': 1000.0,
                'lg(Q(500))': 500.0,
                'lg(Q(300))': 300.0,
                'lg(Q(225))': 225.0,
                'lg(Q(150))': 150.0,
                'lg(Q(75))': 75.0,
                'lg(Q(37.5))': 37.5,
                'lg(Q(18.75))': 18.75,
                'lg(Q(9.375))': 9.375,
                'lg(Q(5.000))': 5.0,
                'lg(Q(2.725))': 2.725}

        def tryfloat(x):
            try:
                return float(x)
            except ValueError:
                return np.nan

        for key in meta:
            result[key].meta = {'Temperature (K)': meta[key]}
            result[key] = np.array([tryfloat(val) for val in result[key]])

        result.meta = {'Temperature (K)': [1000., 500., 300., 225., 150., 75.,
                                           37.5, 18.75, 9.375, 5., 2.725]}

        return result


CDMS = CDMSClass()


def build_lookup():

    result = CDMS.get_species_table()
    keys = list(result[1][:])  # convert NAME column to list
    values = list(result[0][:])  # convert TAG column to list
    dictionary = dict(zip(keys, values))  # make k,v dictionary
    lookuptable = lookup_table.Lookuptable(dictionary)  # apply the class above

    return lookuptable

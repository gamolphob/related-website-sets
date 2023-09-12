# Copyright 2022 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.     
# See the License for the specific language governing permissions and
# limitations under the License.
import json
import requests
from FpsSet import FpsSet
from jsonschema import validate
from urllib.request import urlopen
from urllib.request import Request
from publicsuffix2 import PublicSuffixList

WELL_KNOWN = "/.well-known/first-party-set.json"

class FpsCheck:

    """Stores and runs checks on the list of fps sites

  Attributes:
    fps_sites: A json file read from canonical_sites that should contain all 
    submitted first party sets
    etlds: A string of effective top level domains read from public suffix list
    icanns: A set of domains associated with country codes
    schema: Static. Stores schema for format the canonical_sites should follow
    error_list: Stores all exceptions and issues generated by the checks. This
                allows the issues to be shared in full when iterated through
                without any given check failing halfway through and not 
                catching other issues. 
  """
    

    def __init__(self, fps_sites: json, etlds: PublicSuffixList, icanns: set):
        """Stores the input from canonical_sites, effective_tld_names.dat, and 
        ICANN_domains into the FpsCheck object"""
        self.fps_sites = fps_sites
        self.etlds = etlds
        self.icanns = icanns
        self.error_list = []

    def validate_schema(self, schema_file):
        """Validates the canonical sites list

        Calls the validate function from the jsonschema package on the input 
        from canonical_sites against our predertermined schema

        Args:
            self
        Returns:
            None
        Raises:
            jsonschema.exceptions.ValidationError if the schema does not match 
            the format stored in SCHEMA 
        """
        with open(schema_file) as f:
            SCHEMA = json.loads(f.read())
        validate(self.fps_sites, schema = SCHEMA)

    def load_sets(self):
        """Loads sets from the JSON file into a dictionary of primary->FpsSet

        Loads the sets from fps_list into check_sets, a dictionary of 
        string->FpSet, where the key is the primary of the FpsSet
        If any given primary is listed multiple times, will append an error to 
        the error_list for any primary past the first

        Args:
            None
        Returns:
            Dict[string, FpsSet]
        """
        check_sets = {}
        load_sets_errors = []
        for fpset in self.fps_sites['sets']:
            primary = fpset.get('primary')
            ccTLDs = fpset.get('ccTLDs')
            associated_sites = fpset.get('associatedSites')
            service_sites = fpset.get('serviceSites')
            if primary in check_sets.keys():
                load_sets_errors.append(
                    primary + " is already a primary of another site")
            else:
                check_sets[primary] = FpsSet(
                    ccTLDs, primary, associated_sites, service_sites)
        self.error_list += load_sets_errors
        return check_sets

    def has_all_rationales(self, check_sets):
        """Checks for the presence of all rationaleBySite elements in schema

        Reads the associated sites and service sites from all FpsSets, and 
        checks that they have a corresponding entry in their Fps' 
        rationaleBysites field. If any given site does not have a rationale, 
        or the field is not present when it should be, appends an error to the 
        error_list

        Args:
            Dict[string, FpsSet]
        Returns:
            None
        """
        for fpset in self.fps_sites['sets']:
            if fpset['primary'] not in  check_sets:
                continue
            sites = fpset.get("associatedSites", []) + fpset.get("serviceSites", [])
            rationales = fpset.get('rationaleBySite', None)
            if sites and rationales!=None:
                for site in sites:
                    if site not in rationales.keys():
                        self.error_list.append(
                            "There is no provided rationale for " + site)
            if sites!=None and rationales == None:
                self.error_list.append(
                    "A rationaleBySite field is required for this set, but"
                    + " none is provided. ")

    def check_exclusivity(self, check_sets):
        """This method checks for exclusivity of each field in a set of FpsSets

        Ensures that no FpsSets intersect, e.g. a primary of one set cannot be 
        an associated site of another, nor can it be the primary of another set
        etc. If any sets intersect, information about the intersections is 
        added to the error_list.

        Args:
            check_sets: Dict[string, FpsSet]
        Returns:
            None
        """
        site_list = set()
        for primary in check_sets.keys():
            fps = check_sets[primary]
            # Check the primary
            if primary in site_list:
                self.error_list.append(
                    "This primary is already registered in another first party"
                    + " set: " + primary)
            else:
                site_list.update(primary)
            # Check the associated sites
            if fps.associated_sites:
                associated_overlap = set(fps.associated_sites) & site_list
                if associated_overlap:
                    self.error_list.append(
                        "These associated sites are already registered in " + 
                        "another first party set: " + str(associated_overlap))
                else:
                    site_list.update(fps.associated_sites)
            # Check the service sites
            if fps.service_sites:
                service_overlap = set(fps.service_sites) & site_list
                if service_overlap:
                    self.error_list.append(
                        "These service sites are already registered in another"
                        + " first party set: " + str(service_overlap))
                else:
                    site_list.update(fps.service_sites)
            # Check the ccTLDs
            if fps.ccTLDs:
                for aliased_site in fps.ccTLDs.keys():
                    alias_sites = fps.ccTLDs[aliased_site]
                    alias_overlap = set(alias_sites) & site_list
                    if alias_overlap:
                        self.error_list.append(
                            "These ccTLD sites are already registered in "
                            + "another first party set: " + str(alias_overlap))
                    else:
                        site_list.update(alias_sites)

    def url_is_https(self, site):
        """A function that checks for https://

        Reads a domain name and returns whether or not it begins with https://
        
        Args:
            site: string corresponding to a domain name
        Returns:
            boolean with truth value if the domain name begins with https://
        """
        return site.startswith("https://")

    def find_non_https_urls(self, check_sets):
        """Checks for https:// in all sites. 

        Calls url_is_https on all sites in each FpsSet contained in check_sets,
        and appends errors to the error list for any that return false

        Args:
            check_sets: Dict[string, FpsSet]
        Returns:
            None
        """
        for primary in check_sets:
            # Apply to the primary
            if not self.url_is_https(primary):
                self.error_list.append(
                    "The provided primary site does not begin with https:// " 
                    + primary)
            # Apply to the country codes
            if check_sets[primary].ccTLDs:
                for alias in check_sets[primary].ccTLDs:
                    if not self.url_is_https(alias):
                        self.error_list.append(
                            "The provided alias does not begin with https:// " 
                            + alias)
                    for aliased_site in check_sets[primary].ccTLDs[alias]:
                        if not self.url_is_https(aliased_site):
                            self.error_list.append(
                                "The provided alias site does not begin with" +
                                " https:// " + aliased_site)
            # Apply to associated sites
            if check_sets[primary].associated_sites:
                for associated_site in check_sets[primary].associated_sites:
                    if not self.url_is_https(associated_site):
                        self.error_list.append(
                            "The provided associated site does not begin with"
                             + " https:// " + associated_site)
            # Apply to service sites
            if check_sets[primary].service_sites:
                for service_site in check_sets[primary].service_sites:
                    if not self.url_is_https(service_site):
                        self.error_list.append(
                            "The provided service site does not begin with"
                            + " https:// " + service_site)

    def is_eTLD_Plus1(self, site):
        """A helper function for checking if a domain is etld+1 compliant

        calls get_public suffix from the publicsuffix2 package on the provided
        domain name, returns true if the domain name contains a public suffix,
        else false

        Args:
            site: a string corresponding to a domain name
        Returns:
            boolean with truth value dependent on value of get_public_suffix
        """
        assert site is not None
        site = site.removeprefix("https://")
        is_etldp1_or_etld = self.etlds.get_sld(site, strict=True) == site
        is_etld = self.etlds.get_tld(site, strict=True) == site
        return is_etldp1_or_etld and not is_etld
    

    def find_invalid_eTLD_Plus1(self, check_sets):
        """Checks if all domains are etld+1 compliant

        Calls is_eTLD_Plus1 on all sites in each FpsSet contained in check_sets
        and appends errors to the error list for any that return false

        Args:
            check_sets: Dict[string, FpsSet]
        Returns:
            None
        """
        for primary in check_sets:
            # Apply to the primary
            if not self.is_eTLD_Plus1(primary):
                self.error_list.append(
                    "The provided primary site is not an eTLD+1: " +
                    primary)
            # Apply to the country codes
            if check_sets[primary].ccTLDs:
                for alias in check_sets[primary].ccTLDs:
                    if not self.is_eTLD_Plus1(alias):
                        self.error_list.append(
                            "The provided alias is not an eTLD+1: " +
                            alias)
                    for aliased_site in check_sets[primary].ccTLDs[alias]:
                        if not self.is_eTLD_Plus1(aliased_site):
                            self.error_list.append(
                                "The provided aliased site is not an eTLD+1: " 
                                + aliased_site)
            # Apply to associated sites
            if check_sets[primary].associated_sites:
                for associated_site in check_sets[primary].associated_sites:
                    if not self.is_eTLD_Plus1(associated_site):
                        self.error_list.append(
                            "The provided associated site is not an eTLD+1: " +
                            associated_site)
            # Apply to service sites
            if check_sets[primary].service_sites:
                for service_site in check_sets[primary].service_sites:
                    if not self.is_eTLD_Plus1(service_site):
                        self.error_list.append(
                            "The provided service site is not an eTLD+1: " + 
                            service_site)

    def open_and_load_json(self, url):
        """Calls urlopen and returns json from a site

        Calls urlopena and json.load on a domain. Returns the json object.
        This functionality is separated out here to make testing easier.
        
        Args:
            url: a domain that we want to load the json from
        """
        req = Request(url=url, headers={'User-Agent': 'Chrome'})
        with urlopen(req) as json_file:
            return json.load(json_file)

    def check_list_sites(self, primary, site_list):
        """Checks that sites in a given list have the correct primary on their 
        well-known page
        
        Calls urlopen on a given list of sites, reads their json, and adds any
        sites that do not contain the passed in primary as their listed primary
        to the error list. Also catches and adds any exceptions when trying to
        open or read the url
        
        Args:
            primary: the domain name of the primary site
            site_list: a list of domain names to access
        Returns:
            None
        """
        for site in site_list:
            url = site + WELL_KNOWN
            try:
                json_schema = self.open_and_load_json(url)
                if 'primary' not in json_schema.keys():
                    self.error_list.append(
                        "The listed associated site site did not have primary"
                        + " as a key in its " + WELL_KNOWN
                        + " file: " + site)
                elif json_schema['primary'] != primary:
                    self.error_list.append("The listed associated site "
                    + "did not have " + primary + " listed as its primary: " 
                    + site)
            except Exception as inst:
                self.error_list.append(
                    "Experienced an error when trying to access " + url + "; "
                    + "error was: " + str(inst))
    
    def check_well_known_list(self, field, list1, list2):
        """Checks that 2 lists for a given field match each other
        
        Applies a symmetric diff to list1 and list2 and returns an empty list
        if the 2 fields are symmetric. Otherwise returns a list with a single
        string to be used as error text, containing the field, list1, list2, 
        and their symmetric diff.

        Args:
            field: string
            list1: list[string]
            list2: list[string]
        Returns:
            list[string]
        """
        if list1 == list2:
            return []
        diff = sorted(set(list1) ^ set(list2))
        return ["Encountered an inequality between the PR submission and the " 
        + WELL_KNOWN + " file:\n\t" + field + " was " + str(list1) + " in the PR, and "
        + str(list2) + " in the well-known.\n\tDiff was: " + str(diff) + "."]

    def find_invalid_well_known(self, check_sets):
        """Checks for and validates well-known pages for FPS sets

        Checks for a ./well-known page for first party sets under each
        domain, and checks that the format of the file aligns with the provided
        pages in the canonical list.
        Calls check_list_sites on all ccTLDs, associated, and service sites.
        Appends to the error_list whenever a site is unreachable, an incorrect
        format, or its contents do no match what is expected.

        Args:
            check_sets: Dict[string, FpsSet]
        Returns:
            None
        """
        # Check the schema to ensure consistency
        for primary in check_sets:
            # First we check the primary sites
            url = primary + WELL_KNOWN
            # Read the well-known files and check them against the schema we 
            # have stored
            try:
                json_schema = self.open_and_load_json(url)
                curr_fps_set = check_sets[primary]
                well_known_set = FpsSet(
                    json_schema.get('ccTLDs'), 
                    json_schema.get('primary'), 
                    json_schema.get('associatedSites'), 
                    json_schema.get('serviceSites'))
                if well_known_set.primary != curr_fps_set.primary:
                    self.error_list.append("The " + WELL_KNOWN + " set's " + 
                    "primary (" + well_known_set.primary + ") did not equal " +
                    "the PR set's primary (" + curr_fps_set.primary + ")")
                self.error_list.extend(self.check_well_known_list(
                    "associatedSites",
                    curr_fps_set.associated_sites, 
                    well_known_set.associated_sites
                    )
                )
                self.error_list.extend(self.check_well_known_list(
                    "serviceSites",
                    curr_fps_set.service_sites, 
                    well_known_set.service_sites
                    )
                )
                for aliased_site in curr_fps_set.ccTLDs | well_known_set.ccTLDs:
                    self.error_list.extend(self.check_well_known_list(
                        aliased_site + " alias list",
                        curr_fps_set.ccTLDs.get(aliased_site, []),
                        well_known_set.ccTLDs.get(aliased_site, [])
                        )
                    )
            except Exception as inst:
                self.error_list.append(
                    "Experienced an error when trying to access " + url + 
                    "; error was: " + str(inst))
            # Check the member sites -
            # Now we check the associated sites
            if check_sets[primary].associated_sites:
                self.check_list_sites(
                    primary, check_sets[primary].associated_sites)
            # Now we check the service sites
            if check_sets[primary].service_sites:
                self.check_list_sites(
                    primary, check_sets[primary].service_sites)
            # Now we check the ccTLDs
            if check_sets[primary].ccTLDs:
                ccTLD_sites = []
                for aliased_site in check_sets[primary].ccTLDs:
                    ccTLD_sites += check_sets[primary].ccTLDs[aliased_site]
                    self.check_list_sites(primary, ccTLD_sites)
        
    def find_invalid_removal(self, subtracted_sets):
        """Checks that any sets being removed were properly removed by owner
        
        Checks that the /.well-known page for the primary of any FPS removed
        from the list returns an error 404.
        Args:
            subtracted_sets: Dict[string, FpsSet]
        Returns:
            None"""
        for primary in subtracted_sets:
            url = primary + WELL_KNOWN
            try:
                r = requests.get(url, timeout=10)
                if r.status_code != 404:
                    self.error_list.append("The set associated with " + primary
                            + " was removed from the list, but " + url + 
                            " does not return error 404.")
            except Exception as inst:
                self.error_list.append("Unexpected error when accessing " +
                                    url + "; Received error:" + str(inst))

    def find_invalid_alias_eSLDs(self, check_sets):
        """Checks that eSLDs match their alias, and that country codes are 
        members of icann
        Reads the ccTLDs and makes sure that they match their equivalent sites,
        and that their eTLDs are part of ICANN's list of country codes.
        If either of these is not the case, appends an error to the error_list.
        Note: A site may list a variant with "com" as its eTLD IFF the site 
        being aliased has an eTLD on ICANN's list of countrycodes. 
        Args:
            check_sets: Dict[string, FpsSet]
        Returns:
            None
        """
        for primary, curr_set in check_sets.items():
            if not curr_set.ccTLDs:
                continue
            for aliased_site in curr_set.ccTLDs:
                # first check if the aliased site is actually anywhere else
                # in the fps
                if not curr_set.includes(aliased_site, False):
                    self.error_list.append(
                        "The aliased site " + aliased_site + 
                        " contained within the ccTLDs must be a " +
                        "primary, associated site, or service site " +
                        "within the firsty pary set for " + primary)
                # check the validity of the aliases
                aliased_eSLD, aliased_tld = (aliased_site.split(".")[0],
                                                aliased_site.split(".")[-1])
                if aliased_tld in self.icanns:
                    icann_check = self.icanns.union({"com"})
                else:
                    icann_check = self.icanns
                variants = [(site, site.split(".")[0], site.split(".")[-1])
                            for site in curr_set.ccTLDs[aliased_site]]
                for site, eSLD, tld in variants:
                    if eSLD != aliased_eSLD:
                        self.error_list.append(
                            "The following top level domain must match: " 
                            + aliased_site + ", but is instead: " 
                            + site)
                    if tld not in icann_check:
                        self.error_list.append(
                            "The provided country code: " + tld + 
                            ", in: " + site + 
                            " is not a ICANN registered country code")

    def find_robots_txt(self, check_sets):
        """Checks service sites to see if they have a robots.txt subdomain.


        Iterates through all service_sites in each FpsSet provided, and makes
        a get request to site/robots.txt for each. This request should return
        an error 4xx, 5xx, or a timeout error. If it does not, and the page 
        does exist, then it is expected that the site contains a X-Robots-Tag
        in its header. If none of these conditions is met, an error is appended
        to the error list.

        Args:
            check_sets: Dict[string, FpsSet]
        Returns:
            None
        """
        exception_retries = "Max retries exceeded with url: /robots.txt"
        exception_timeout = "Read timed out. (read timeout=10)"
        for primary in check_sets:
            if not check_sets[primary].service_sites:
                continue
            for service_site in check_sets[primary].service_sites:
                try:
                    r_service = requests.get(service_site, timeout=10)
                    if 'X-Robots-Tag' not in r_service.headers:
                        self.error_list.append("The service site " + 
                        service_site + " does not have an X-Robots-Tag in its "
                         + "header")
                    else:
                        robots_tag = r_service.headers['X-Robots-Tag']
                        if ':' in robots_tag:
                            self.error_list.append("The service site " + 
                                service_site + " contains an 'X-Robots-Tag' " +
                                "that does not meet the policy requirements")
                        elif 'none' not in robots_tag and 'noindex' not in robots_tag:
                                    self.error_list.append("The service site " 
                                        + service_site + " does not have a " +
                                        "'noindex' or 'none' tag in its header"
                                        )
                except Exception as inst:
                    if exception_retries not in str(inst):
                        if exception_timeout not in str(inst):
                            self.error_list.append(
                                "Unexpected error for service site: " +
                                    service_site + "; Received error:" + 
                                    str(inst))

    def find_ads_txt(self, check_sets):
        """Checks to see if service sites have an ads.txt subdomain. 

        Iterates through all service_sites in each FpsSet provided, and makes
        a get request to site/ads.txt for each. Appends errors to the error 
        list for any that do not return an error 4xx or 5xx or if the site
        does not cause a timeout error. 

        Args:
            check_sets: Dict[string, FpsSet]
        Returns:
            None
        """

        exception_retries = "Max retries exceeded with url: /ads.txt"
        exception_timeout = "Read timed out. (read timeout=10)"
        for primary in check_sets:
            if not check_sets[primary].service_sites:
                continue
            for service_site in check_sets[primary].service_sites:
                ads_site = service_site + "/ads.txt"
                try:
                    r = requests.get(ads_site, timeout=10)
                    if r.status_code == 200:
                        self.error_list.append("The service site " + 
                        service_site + " has an ads.txt file, this violates "
                        + "the policies for service sites")
                except Exception as inst:
                    if exception_retries not in str(inst):
                        if exception_timeout not in str(inst):
                            self.error_list.append(
                                "Unexpected error for service site: " +
                                service_site + "\nReceived error:" + str(inst))

    def check_for_service_redirect(self, check_sets):
        """Checks to see if service sites redirect to another site
        or return a user/server error.
        
        Makes a get request to all service sites in each FpsSet contained in 
        check_sets, and appends errors to the error list for any that do not 
        return an error 4xx or 5xx or if the site does not cause a timeout 
        error. 

        Args:
            check_sets: Dict[string, FpsSet]
        Returns:
            None
        """

        exception_retries = "Max retries exceeded with url: /"
        exception_timeout = "Read timed out. (read timeout=10)"
        for primary in check_sets:
            if not check_sets[primary].service_sites:
                continue
            for service_site in check_sets[primary].service_sites:
                try:
                    r = requests.get(service_site, timeout=10)
                    # We want the request status_code to be a 4xx or 5xx, raise
                    # an exception if it's outside that range
                    if r.status_code < 400 or r.status_code >= 600:
                        # If a get request to a service site successfully 
                        # connects to that site, we expect it to be a redirect
                        # If it is not a redirect, we raise an exception
                        if r.url == service_site or r.url == service_site+"/":
                            self.error_list.append(
                                "The service site must not be an endpoint: " + 
                                service_site)
                except Exception as inst:
                    if exception_retries not in str(inst):
                        if exception_timeout not in str(inst):
                            self.error_list.append("Unexpected error for "
                            + "service site: " + service_site + 
                            "\nReceived error: " + str(inst))

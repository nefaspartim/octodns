#
#
#

from __future__ import absolute_import, division, print_function, \
    unicode_literals

## octodns specfic imports:
import requests
from akamai.edgegrid import EdgeGridAuth
from urlparse import urljoin
import json
from collections import defaultdict

import logging
from ..record import Record
from .base import BaseProvider   


class AkamaiClientException(Exception):
    
    _errorMessages = {
        400: "400: Bad request", 
        401: "401: Unauthorized",
        403: "403: Access is forbidden",
        404: "404: Resource not found",
        405: "405: Method not supported",
        406: "406: Not Acceptable",
        409: "409: Request not allowed due to conflict with current state of resource",
        415: "415: Unsupported media type",
        422: "422: Request body contains an error preventing processing",
        500: "500: Internal server error"
    }

    def __init__(self, code):
        message = self._errorMessages.get(code)
        super(AkamaiClientException, self).__init__(message)


class AkamaiClient(object):

    def __init__(self, _client_secret, _host, _access_token, _client_token):

        self.base = "https://" + _host + "/config-dns/v2/"
        self.basev1 = "https://" + _host + "/config-dns/v1/"
        self.basehost = "https://" + _host

        sess = requests.Session()
        sess.auth = EdgeGridAuth(
            client_token=_client_token,
            client_secret=_client_secret,
            access_token=_access_token
        )
        self._sess = sess


    def _request(self, method, path, params=None, data=None, v1=False):
        
        url = urljoin(self.base, path)
        if v1: 
            url = urljoin(self.basev1, path)

        resp = self._sess.request(method, url, params=params, json=data)

        if resp.status_code > 299:
            # print(resp.status_code)
            raise AkamaiClientException(resp.status_code)
        resp.raise_for_status()


        return resp
 

    def record_get(self, zone, name, record_type):
        
        path = 'zones/{}/names/{}/types/{}'.format(zone, name, record_type)
        result = self._request('GET', path)
        
        return result

    def record_create(self, zone, name, record_type, params):
        path = 'zones/{}/names/{}/types/{}'.format(zone, name, record_type)
        result = self._request('POST', path, data=params)

        return result

    def record_delete(self, zone, name, record_type):
        path = 'zones/{}/names/{}/types/{}'.format(zone, name, record_type)
        print(path)
        result = self._request('DELETE', path)
        
        if result.status_code == 204:
            print ("successfully deleted ", path)

        return result

    def record_replace(self, zone, name, record_type, params):
        path = 'zones/{}/names/{}/types/{}'.format(zone, name, record_type)
        result = self._request('PUT', path, data=params)

        return result


    def zone_get(self, zone):
        path = 'zones/{}'.format(zone)
        result = self._request('GET', path)

        return result

    def zone_create(self, contractId, params, gid=None):
        path = 'zones?contractId={}'.format(contractId)

        if gid is not None:
            path += '&gid={}'.format(gid)
        
        result = self._request('POST', path, data=params)
        
        return result


    def zones_get(self, contractIds=None, page=None, pageSize=None, search=None,
                     showAll="true", sortBy="zone", types=None):
        path = 'zones'

        params = {
            'contractIds': contractIds,
            'page': page, 
            'pageSize': pageSize,
            'search': search,
            'showAll': showAll,
            'sortBy': sortBy,
            'types': types
        }

        result = self._request('GET', path, params=params)

        return result

    def zone_recordset_get(self, zone, page=None, pageSize=30, search=None,
                     showAll="true", sortBy="name", types=None):

        params = {
            'page': page, 
            'pageSize': pageSize,
            'search': search,
            'showAll': showAll,
            'sortBy': sortBy,
            'types': types
        }

        path = 'zones/{}/recordsets'.format(zone)
        result = self._request('GET', path, params=params)


        return result


    def contracts_get(self, gid=None):
        path = 'data/contracts'
        if gid is not None:
            path += '?gid={}'.format(gid)

        # result = self._sess.request('GET', path)

        result = self._request('GET', path)

        return result

    def recordsets_get(self, zone_name):
        
        resp = self.zone_recordset_get(zone_name, showAll="true")
        recordset = resp.json().get("recordsets")

        return recordset

    def master_zone_file_get(self, zone):
        
        path = 'zones/{}/zone-file'.format(zone)

        try:
            result = self._request('GET', path)

        except AkamaiClientException as e:
            # not working with API v2, API v1 fallback
            path = 'zones/{}'.format(zone)
            result = self._request('GET', path, v1=True)
            print("Using API v1 fallback")
            print("(Probably Ignore)", e.message)

        return result


class AkamaiProvider(BaseProvider):

    SUPPORTS_GEO = False
    SUPPORTS_DYNAMIC = False

    SUPPORTS = set(('A', 'AAAA', 'CNAME', 'MX', 'NAPTR', 'NS', 'PTR', 'SPF', 
                    'SRV', 'SSHFP', 'TXT'))

    def __init__(self, id, client_secret, host, access_token, client_token, 
                contract_id=None, gid=None, *args, **kwargs):
        
        self.log = logging.getLogger('AkamaiProvider[{}]'.format(id))
        self.log.debug('__init__: id=%s, ')
        super(AkamaiProvider, self).__init__(id, *args, **kwargs)

        self._dns_client = AkamaiClient(client_secret, host, access_token, 
                    client_token)
        
        self._zone_records = {}
        self._contractId = contract_id
        self._gid = gid

    def zone_records(self, zone):
        """ returns records for a zone, finds it if not present, or 
            returns empty if can't find a match
        """
        if zone.name not in self._zone_records:
            try:
                name = zone.name[:-1]
                self._zone_records[zone.name] = self._dns_client.recordsets_get(name)

            except AkamaiClientException:
                return []

        return self._zone_records[zone.name]

    def populate(self, zone, target=False, lenient=False):
        self.log.debug('populate: name=%s', zone.name)

        values = defaultdict(lambda: defaultdict(list))
        for record in self.zone_records(zone):
            
            _type =record.get('type')
            ## Akamai sends down prefix.zonename., while OctoDNS only expects prefix
            _name = record.get('name').split("." + zone.name[:-1], 1)[0] 
            if _name == zone.name[:-1] :
                _name = ''  ## root / @

            if _type not in self.SUPPORTS:
                continue
            values[_name][_type].append(record)

        before = len(zone.records)
        for name, types in values.items():
            for _type, records in types.items():
                data_for = getattr(self, '_data_for_{}'.format(_type))
                record = Record.new(zone, name, data_for(_type, records[0]), 
                                source=self, lenient=lenient)
                zone.add_record(record, lenient=lenient)
        
        exists = zone.name in self._zone_records
        found = len(zone.records) - before
        self.log.info('populate:    found %s records, exists=%s', found, exists)

        return exists

    def _data_for_multiple(self, _type, records):
        
        return {
            'ttl':records['ttl'],
            'type': _type, 
            'values': [r for r in records['rdata']]
        } 

    _data_for_A = _data_for_multiple
    _data_for_AAAA = _data_for_multiple
    _data_for_NS = _data_for_multiple
    _data_for_SPF = _data_for_multiple

    def _data_for_CNAME(self, _type, records): 
        value =records['rdata'][0]
        if (value[-1] != '.') :
            value = '{}.'.format(value)

        return {
            'ttl':records['ttl'],
            'type': _type,
            'value': value
        }

    def _data_for_MX(self, _type, records):
        values = []
        for r in records['rdata']:
            preference, exchange = r.split(" ", 1)
            values.append({
                'preference': preference,
                'exchange' : exchange
            }) 
        return {
            'ttl':records['ttl'],
            'type': _type,
            'values': values
        }

    def _data_for_NAPTR(self, _type, records):
        values = []
        for r in records['rdata']:
            order, preference, flags, service, regexp, repl = r.split(' ', 5)
            
            values.append({
                'flags': flags[1:-1],
                'order': order,
                'preference': preference,
                'regexp': regexp[1:-1],
                'replacement': repl, 
                'service': service[1:-1]
            })
        return {
            'type': _type,
            'ttl':records['ttl'],
            'values': values
        }
    
    def _data_for_PTR(self, _type, records):

        return {
            'ttl':records['ttl'],
            'type': _type,
            'value' :records['rdata'][0]
        } 
    
    def _data_for_SRV(self, _type, records):
        values = []
        for r in records['rdata']:
            priority, weight, port, target = r.split(' ', 3)
            values.append({
                'port': port,
                'priority': priority,
                'target': target,
                'weight': weight
            })
        return {
            'type': _type,
            'ttl':records['ttl'],
            'values': values
        }

    def _data_for_SSHFP(self, _type, records):
        values = []
        for r in records['rdata']:
            algorithm, fp_type, fingerprint = r.split(' ', 2)
            values.append({
                'algorithm': algorithm,
                'fingerprint': fingerprint,
                'fingerprint_type': fp_type
            })
        return {
            'type': _type,
            'ttl': records['ttl'],
            'values': values 
        }

    def _data_for_TXT(self, _type, records):
        values = []
        for r in records['rdata']:
            r = r[1:-1]
            values.append(r.replace(';', '\\;'))

        return {
            'ttl': records['ttl'],
            'type': _type,
            'values': values
        }
    

    def _apply(self, plan):
        desired = plan.desired
        changes = plan.changes
        self.log.debug('_apply: zone=%s, len(changes)=%d', desired.name,
                        len(changes))

        zone_name = desired.name[:-1]
        print(zone_name)


        try:
            self._dns_client.zone_get(zone_name)

        except AkamaiClientException as e:
            
            print("zone not found, creating zone")
            self.log.debug('_apply:   no matching zone, creating zone')
            params = self._build_zone_config(zone_name)
            self._dns_client.zone_create(self._contractId, params, self._gid)

        for change in changes:
            class_name = change.__class__.__name__
            print()
            print(class_name)
            if (class_name == "Delete" ):
                print(change.existing.name)
                print(change.existing._type)
                print(change.existing.data)
            elif class_name == "Update":
                print(change.existing.name)
                print(change.existing._type)
                print(change.existing.data)
                print("----------------->")
                print(change.new.name)
                print(change.new._type)
                print(change.new.data)
            else:
                print(change.new.name)
                print(change.new._type)
                print(change.new.data)
            print()
            print(change)
            print()
            getattr(self, '_apply_{}'.format(class_name))(change)

        # Clear out the cache if any
        self._zone_records.pop(desired.name, None)

    def _apply_Create(self, change):
        new = change.new
        params_for = getattr(self, '_params_for_{}'.format(new._type))
        for params in params_for(new):
            pass
            # self._dns_client.record_create(new.zone.name[:-1], params)

    def _apply_Delete(self, change):
        existing = change.existing
        zone = existing.zone.name[:-1]
        name = existing.name + '.' + zone
        record_type = existing._type

        result = self._dns_client.record_delete(zone, name, record_type)

        return result

    def _build_zone_config(self, zone, _type=None, comment=None, masters=[]):

        if _type is None: 
            _type="primary"
        
        if self._contractId is None:
            self._set_default_contractId()

        return {
            "zone": zone,
            "type": _type,
            "comment": comment,
            "masters": masters
        }

    def _set_default_contractId(self):
        ''' if no contractId is set, but one is required to create a new zone, 
            this function will try to retrieve any contracts available to the
            user, and use the first one it finds
        '''
        
        try:

            request = self._dns_client.zones_get(self._gid)
            response = request.json()
            zones = response['zones']

            contractId = zones[0]['contractId']


            self._contractId = contractId
            self.log.info("contractId not specified, using contractId=%s", contractId)
        
        except KeyError:
            self.log.debug("_get_default_contractId: key error")
            raise

        except:
            self.log.debug("_get_default_contractId: unable to find a contractId")
            raise 

        return


    def _test(self, zone) :

        zone_name = zone.name[:len(zone.name)-1]

        record_name = "octo.basir-test.com"
        record_type = "A"
        params = {
            "name": "octo.basir-test.com",
            "type": "A",
            "ttl": 300,
            "rdata": [
                "10.0.0.2",
                "10.0.0.3"
            ]
        }
        repl_params = {
            "name": "octo.basir-test.com",
            "type": "A",
            "ttl": 300,
            "rdata": [
                "99.99.99.99",
                "10.0.0.3",
                "1.2.3.4"
            ]
        }


        print("\n\nRunning test: record get..........\n")
        self._test_record_get(zone_name, "test.basir-test.com", record_type)
        print("\n\nRunning test: record create..........\n")
        self._test_record_create(zone_name, record_name, record_type, params)
        print("\n\nRunning test: record replace..........\n")
        self._test_record_replace(zone_name, record_name, record_type, repl_params)
        print("\n\nRunning test: record delete..........\n")
        self._test_record_delete(zone_name, record_name, record_type)

        print("\n\nRunning test: zones get..........\n")
        self._test_zones_get()

        print("\n\nRunning test: zone recordset get..........\n")
        self._test_zones_recordset_get(zone_name)

        print("\n\nRunning test: Master Zone File get..........\n")
        self._test_master_zone_file_get(zone_name)

        return

    def _test_record_get(self, zone_name, record_name, record_type):
        try:
            get = self._dns_client.record_get(zone_name, record_name, record_type)
        except AkamaiClientException as e:
            print ("record get test failed")
            print (e.message)

        else:
            print("record get test result: ")
            print(json.dumps(get.json(), indent=4, separators=(',', ': ')))

        return

    def _test_record_delete(self, zone_name, record_name, record_type):

        try:
            delete = self._dns_client.record_delete(zone_name, record_name, record_type)
        except AkamaiClientException as e:
            print("delete failed")
            print(e.message)
            return


        try:
            self._dns_client.record_get(zone_name, record_name, record_type)
        except AkamaiClientException as e:
            print("get on record failed as expected, since record was succesfully deleted")
            print ("(Probably Ignore):", e.message)
            print ("delete status:", delete.status_code)
        else:
            print("unexpected condition in test delete")

        return

    def _test_record_create(self, zone_name, record_name, record_type, params):

        try:
            create  = self._dns_client.record_create(zone_name, record_name, record_type, params)
        except AkamaiClientException as e:
            print ("create unsuccessful, presumably because it already exists")
            print ("(Probably Ignore)", e.message)
        else:
            print("initial create of", create.json().get("name"), "succesful: ", create.status_code)

        return

    def _test_record_replace(self, zone_name, record_name, record_type, params):

        ## create record to be replaced, if it doesn't already exist
        try:
            old_params = {
                "name": record_name,
                "type": record_type,
                "ttl": 300,
                "rdata": [
                    "10.0.0.2",
                    "10.0.0.3"
                ]
            }
            create = self._dns_client.record_create(zone_name, record_name, record_type, old_params)
        except AkamaiClientException as e:
            print ("initial create unsuccessful, presumably because it already exists")
            print ("(Probably Ignore)", e.message)
        else:
            print("initial create of record to be replaced", create.json().get("name"), "succesful: ", create.status_code)

        
        ## test replace
        try:
            replace = self._dns_client.record_replace(zone_name, record_name, record_type, params)
        except AkamaiClientException as e:
            print("replace failed")
            print(e.message)
            return
        else:
            try:
                record = self._dns_client.record_get(zone_name, record_name, record_type)
            except AkamaiClientException as e:
                print("retrieval in replacement failed")
                print(e.message)
            else:
                new_data = record.json()

                if (new_data != params):
                    print("replace failed, records don't match")
                    print("current data:")
                    print(new_data)
                    print("expected data:")
                    print(params)
                
                else:
                    print("replace succesful")
                    print("replace status:", replace.status_code)

    def _test_zones_get(self):
        try:
            zonesList = self._dns_client.zones_get()
        except AkamaiClientException as e:
            print ("zones get test failed") 
            print (e.message)

        else:
            print("zones list: ")
            print(json.dumps(zonesList.json(), indent=4, separators=(',', ': ')))

        return

    def _test_zones_recordset_get(self, zone_name):
        try:
            zoneRecordset = self._dns_client.zone_recordset_get(zone_name)
        except AkamaiClientException as e:
            print("zone recordset retrieval test failed")
            print (e.message)
        else:
            print("zone recordset: ")
            print(json.dumps(zoneRecordset.json(), indent=4, separators=(',', ': ')))
        return

    def _test_master_zone_file_get(self, zone_name):
        try:
            mzf = self._dns_client.master_zone_file_get(zone_name)

        except AkamaiClientException as e:
            print("MZF retrieval test failed")
            print (e.message)

        else:
            print("Master Zone File:")
            print(json.dumps(mzf.json(), indent=4, separators=(',', ': ')))

        return 

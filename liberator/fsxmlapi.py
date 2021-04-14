import traceback
import json
import copy

import redis
from fastapi import APIRouter, Request, Response
from fastapi.templating import Jinja2Templates

from configuration import (ESL_HOST, ESL_PORT, ESL_SECRET,
                           MAX_SPS, MAX_SESSION, FIRST_RTP_PORT, LAST_RTP_PORT,
                           REDIS_HOST, REDIS_PORT, REDIS_DB, REDIS_PASSWORD, SCAN_COUNT)

from utilities import logify, get_request_uuid, hashfieldify, jsonhash, getnameid, listify


REDIS_CONNECTION_POOL = redis.BlockingConnectionPool(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, password=REDIS_PASSWORD, 
                                                     decode_responses=True, max_connections=10, timeout=5)
rdbconn = redis.StrictRedis(connection_pool=REDIS_CONNECTION_POOL)                                                    

# api router declaration
fsxmlrouter = APIRouter()

# template location 
templates = Jinja2Templates(directory="templates/fsxml")

#-----------------------------------------------------------------------------------------------------------------------------------------------------------------------
@fsxmlrouter.get("/fsxmlapi/switch", include_in_schema=False)
def switch(request: Request, response: Response):
    try:
        result = templates.TemplateResponse("switch.j2.xml",
                                            {"request": request, "max_sessions": MAX_SESSION, "sessions_per_second": MAX_SPS, "rtp_start_port": FIRST_RTP_PORT, "rtp_end_port": LAST_RTP_PORT},
                                            media_type="application/xml")
        response.status_code = 200
    except Exception as e:
        response.status_code, result = 500, str()
        logify(f"module=liberator, space=fsxmlapi, section=switch, requestid={get_request_uuid()}, exception={e}, traceback={traceback.format_exc()}")
    finally:
        return result


@fsxmlrouter.get("/fsxmlapi/event-socket", include_in_schema=False)
def esl(request: Request, response: Response):
    try:
        result = templates.TemplateResponse("event-socket.j2.xml",
                                            {"request": request, "host": ESL_HOST, "port": ESL_PORT, "password": ESL_SECRET},
                                            media_type="application/xml")
        response.status_code = 200
    except Exception as e:
        response.status_code, result = 500, str()
        logify(f"module=liberator, space=fsxmlapi, section=event-socket, requestid={get_request_uuid()}, exception={e}, traceback={traceback.format_exc()}")
    finally:
        return result


@fsxmlrouter.get("/fsxmlapi/acl", include_in_schema=False)
def acl(request: Request, response: Response):
    try:
        pipe = rdbconn.pipeline()
        # IP LIST OF INBOUND INTERCONNECTION
        # {sipprofile: [list of ips]}
        KEYPATTERN = 'intcon:in:*'
        next, mainkeys = rdbconn.scan(0, KEYPATTERN, SCAN_COUNT)
        while next:
            next, tmpkeys = rdbconn.scan(next, KEYPATTERN, SCAN_COUNT)
            mainkeys += tmpkeys
        for mainkey in mainkeys:
            pipe.hmget(mainkey, 'sipprofile', 'sip_ips', 'auth_username')
        sipprofile_ips = dict()
        for details in pipe.execute():
            if details:
                if not hashfieldify(details[2]):
                    sipprofile = details[0]
                    sip_ips = hashfieldify(details[1])
                    if sipprofile in sipprofile_ips: sipprofile_ips[sipprofile] += sip_ips
                    else: sipprofile_ips[sipprofile] = sip_ips

        # DEFINED ACL LIST
        # [{'name': name, 'action': default-action, 'rules': [{'action': allow/deny, 'key': domain/cidr, 'value': ip/domain-value}]}]
        KEYPATTERN = 'base:acl:*'
        next, mainkeys = rdbconn.scan(0, KEYPATTERN, SCAN_COUNT)
        while next:
            next, tmpkeys = rdbconn.scan(next, KEYPATTERN, SCAN_COUNT)
            mainkeys += tmpkeys
        for mainkey in mainkeys:
            pipe.hgetall(mainkey)
        defined_acls = list()
        for detail in pipe.execute():
            if detail:
                name = detail.get('name')
                action = detail.get('action')
                rulestrs = hashfieldify(detail.get('rules'))
                rules = list(map(lambda rule: {'action': rule[0], 'key': rule[1], 'value': rule[2]}, map(listify, rulestrs)))
                defined_acls.append({'name': name, 'action': action, 'rules': rules})

        result = templates.TemplateResponse("acl.j2.xml",
                                            {"request": request, "sipprofile_ips": sipprofile_ips, "defined_acls": defined_acls},
                                            media_type="application/xml")
        response.status_code = 200
    except Exception as e:
        response.status_code, result = 500, str()
        logify(f"module=liberator, space=fsxmlapi, section=acl, requestid={get_request_uuid()}, exception={e}, traceback={traceback.format_exc()}")
    finally:
        return result


@fsxmlrouter.get("/fsxmlapi/distributor", include_in_schema=False)
def distributor(request: Request, response: Response):
    try:
        pipe = rdbconn.pipeline()
        KEYPATTERN = 'intcon:out:*:_gateways'
        next, mainkeys = rdbconn.scan(0, KEYPATTERN, SCAN_COUNT)
        while next:
            next, tmpkeys = rdbconn.scan(next, KEYPATTERN, SCAN_COUNT)
            mainkeys += tmpkeys

        for mainkey in mainkeys:
            pipe.hgetall(mainkey)
        details = pipe.execute()

        interconnections = dict()
        for mainkey, detail in zip(mainkeys, details):
            intconname = getnameid(mainkey)
            interconnections[intconname] = jsonhash(detail)

        result = templates.TemplateResponse("distributor.j2.xml",
                                            {"request": request, "interconnections": interconnections},
                                            media_type="application/xml")
        response.status_code = 200
    except Exception as e:
        response.status_code, result = 500, str()
        logify(f"module=liberator, space=fsxmlapi, section=distributor, requestid={get_request_uuid()}, exception={e}, traceback={traceback.format_exc()}")
    finally:
        return result


@fsxmlrouter.get("/fsxmlapi/sip-setting", include_in_schema=False)
def sip(request: Request, response: Response):
    try:
        pipe = rdbconn.pipeline()
        # get the maping siprofile and data
        # {profilename1: profiledata1, profilename2: profiledata2}
        KEYPATTERN = 'sipprofile:*'
        next, mainkeys = rdbconn.scan(0, KEYPATTERN, SCAN_COUNT)
        while next:
            next, tmpkeys = rdbconn.scan(next, KEYPATTERN, SCAN_COUNT)
            mainkeys += tmpkeys

        for mainkey in mainkeys:
            pipe.hgetall(mainkey)
        details = pipe.execute()

        sipprofiles = dict()
        for mainkey, detail in zip(mainkeys, details):
            sipprofiles[getnameid(mainkey)] = jsonhash(detail)

        # get the mapping siprofile name and interconnection name
        # {profilename1: [intconname,...], profilename2: [intconname,...]}
        KEYPATTERN = 'intcon:out:*'
        next, mainkeys = rdbconn.scan(0, KEYPATTERN, SCAN_COUNT)
        while next:
            next, tmpkeys = rdbconn.scan(next, KEYPATTERN, SCAN_COUNT)
            mainkeys += tmpkeys

        for mainkey in mainkeys:
            if not mainkey.endswith('_gateways'):
                pipe.hget(mainkey, 'sipprofile')
        profilenames = pipe.execute()

        profile_intcons_maps = dict()
        for mainkey, profilename in zip(mainkeys, profilenames):
            intconname = getnameid(mainkey)
            if profilename not in profile_intcons_maps:
                profile_intcons_maps[profilename] = [intconname]
            else:
                if profilename not in profile_intcons_maps[profilename]:
                    profile_intcons_maps[profilename].append(profilename)

        # get the mapping siprofile name and gateway name
        # {profilename1: [gateway,...], profilename2: [gateway,...]}
        profile_gwnames_maps = dict()
        for profile, intcons in profile_intcons_maps.items():
            for intcon in intcons:
                pipe.hkeys(f'intcon:out:{intcon}:_gateways')
            profile_gwnames_maps[profile] = list(set([gw for gws in pipe.execute() for gw in gws]))

        # add gateway data to sip profile data
        profile_gateways_maps = dict()
        for profile, gwnames in profile_gwnames_maps.items():
            for gwname in gwnames:
                pipe.hgetall(f'gateway:{gwname}')
            profile_gateways_maps[profile] = list(map(jsonhash, pipe.execute()))

        for sipprofile in sipprofiles:
            sipprofiles[sipprofile]['gateways'] = profile_gateways_maps[sipprofile]

        # template
        result = templates.TemplateResponse("sip-setting.j2.xml",
                                            {"request": request, "sipprofiles": sipprofiles},
                                            media_type="application/xml")
        response.status_code = 200
    except Exception as e:
        response.status_code, result = 500, str()
        logify(f"module=liberator, space=fsxmlapi, section=sip-setting, requestid={get_request_uuid()}, exception={e}, traceback={traceback.format_exc()}")
    finally:
        return result


#!/usr/bin/env python3
#
# Copyright (C) 2018-2021 VyOS maintainers and contributors
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 or later as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
# TODO: merge with show_dhcp.py

from json import dumps
from argparse import ArgumentParser
from ipaddress import ip_address
from tabulate import tabulate
from sys import exit
from collections import OrderedDict
from datetime import datetime

from isc_dhcp_leases import Lease, IscDhcpLeases

from vyos.config import Config
from vyos.util import is_systemd_service_running

lease_file = "/config/dhcpdv6.leases"
pool_key = "shared-networkname"

lease_display_fields = OrderedDict()
lease_display_fields['ip'] = 'IPv6 address'
lease_display_fields['state'] = 'State'
lease_display_fields['last_comm'] = 'Last communication'
lease_display_fields['expires'] = 'Lease expiration'
lease_display_fields['remaining'] = 'Remaining'
lease_display_fields['type'] = 'Type'
lease_display_fields['pool'] = 'Pool'
lease_display_fields['iaid_duid'] = 'IAID_DUID'

lease_valid_states = ['all', 'active', 'free', 'expired', 'released', 'abandoned', 'reset', 'backup']

def in_pool(lease, pool):
    if pool_key in lease.sets:
        if lease.sets[pool_key] == pool:
            return True

    return False

def format_hex_string(in_str):
    out_str = ""

    # if input is divisible by 2, add : every 2 chars
    if len(in_str) > 0 and len(in_str) % 2 == 0:
        out_str = ':'.join(a+b for a,b in zip(in_str[::2], in_str[1::2]))
    else:
        out_str = in_str

    return out_str

def utc_to_local(utc_dt):
    return datetime.fromtimestamp((utc_dt - datetime(1970,1,1)).total_seconds())

def get_lease_data(lease):
    data = {}

    # isc-dhcp lease times are in UTC so we need to convert them to local time to display
    try:
        data["expires"] = utc_to_local(lease.end).strftime("%Y/%m/%d %H:%M:%S")
    except:
        data["expires"] = ""

    try:
        data["last_comm"] = utc_to_local(lease.last_communication).strftime("%Y/%m/%d %H:%M:%S")
    except:
        data["last_comm"] = ""

    try:
        data["remaining"] = lease.end - datetime.utcnow()
        # negative timedelta prints wrong so bypass it
        if (data["remaining"].days >= 0):
            # substraction gives us a timedelta object which can't be formatted with strftime
            # so we use str(), split gets rid of the microseconds
            data["remaining"] = str(data["remaining"]).split('.')[0]
        else:
            data["remaining"] = ""
    except:
        data["remaining"] = ""

    # isc-dhcp records lease declarations as ia_{na|ta|pd} IAID_DUID {...}
    # where IAID_DUID is the combined IAID and DUID
    data["iaid_duid"] = format_hex_string(lease.host_identifier_string)

    lease_types_long = {"na": "non-temporary", "ta": "temporary", "pd": "prefix delegation"}
    data["type"] = lease_types_long[lease.type]

    data["state"] = lease.binding_state
    data["ip"] = lease.ip

    try:
        data["pool"] = lease.sets[pool_key]
    except:
        data["pool"] = ""

    return data

def get_leases(config, leases, state, pool=None, sort='ip'):
    leases = IscDhcpLeases(lease_file).get()

    # filter leases by state
    if 'all' not in state:
        leases = list(filter(lambda x: x.binding_state in state, leases))

    # filter leases by pool name
    if pool is not None:
        if config.exists_effective("service dhcp-server shared-network-name {0}".format(pool)):
            leases = list(filter(lambda x: in_pool(x, pool), leases))
        else:
            print("Pool {0} does not exist.".format(pool))
            exit(0)

    # should maybe filter all state=active by lease.valid here?

    # sort by last_comm time to dedupe (newest lease overrides older)
    leases = sorted(leases, key = lambda lease: lease.last_communication)

    # dedupe by converting to dict
    leases_dict = {}
    for lease in leases:
        # dedupe by IP
        leases_dict[lease.ip] = lease

    # convert the lease data
    leases = list(map(get_lease_data, leases_dict.values()))

    # apply output/display sort
    if sort == 'ip':
        leases = sorted(leases, key = lambda k: int(ip_address(k['ip'].split('/')[0])))
    else:
        leases = sorted(leases, key = lambda k: k[sort])

    return leases

def show_leases(leases):
    lease_list = []
    for l in leases:
        lease_list_params = []
        for k in lease_display_fields.keys():
            lease_list_params.append(l[k])
        lease_list.append(lease_list_params)

    output = tabulate(lease_list, lease_display_fields.values())

    print(output)

if __name__ == '__main__':
    parser = ArgumentParser()

    group = parser.add_mutually_exclusive_group()
    group.add_argument("-l", "--leases", action="store_true", help="Show DHCPv6 leases")
    group.add_argument("-s", "--statistics", action="store_true", help="Show DHCPv6 statistics")
    group.add_argument("--allowed", type=str, choices=["pool", "sort", "state"], help="Show allowed values for argument")

    parser.add_argument("-p", "--pool", type=str, help="Show lease for specific pool")
    parser.add_argument("-S", "--sort", type=str, default='ip', help="Sort by")
    parser.add_argument("-t", "--state", type=str, nargs="+", default=["active"], help="Lease state to show (can specify multiple with spaces)")
    parser.add_argument("-j", "--json", action="store_true", default=False, help="Produce JSON output")

    args = parser.parse_args()

    conf = Config()

    if args.allowed == 'pool':
        if conf.exists_effective('service dhcpv6-server'):
            print(' '.join(conf.list_effective_nodes("service dhcpv6-server shared-network-name")))
        exit(0)
    elif args.allowed == 'sort':
        print(' '.join(lease_display_fields.keys()))
        exit(0)
    elif args.allowed == 'state':
        print(' '.join(lease_valid_states))
        exit(0)
    elif args.allowed:
        parser.print_help()
        exit(1)

    if args.sort not in lease_display_fields.keys():
        print(f'Invalid sort key, choose from: {list(lease_display_fields.keys())}')
        exit(0)

    if not set(args.state) < set(lease_valid_states):
            print(f'Invalid lease state, choose from: {lease_valid_states}')
            exit(0)

    # Do nothing if service is not configured
    if not conf.exists_effective('service dhcpv6-server'):
        print("DHCPv6 service is not configured")
        exit(0)

    # if dhcp server is down, inactive leases may still be shown as active, so warn the user.
    if not is_systemd_service_running('isc-dhcp-server6.service'):
        print("WARNING: DHCPv6 server is configured but not started. Data may be stale.")

    if args.leases:
        leases = get_leases(conf, lease_file, args.state, args.pool, args.sort)

        if args.json:
            print(dumps(leases, indent=4))
        else:
            show_leases(leases)
    elif args.statistics:
        print("DHCPv6 statistics option is not available")
    else:
        parser.print_help()
        exit(1)

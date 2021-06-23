import json
from regipy.registry import RegistryHive
from facs.bo.abstract import AbstractBo


class RegistryBo(AbstractBo):
    __CONNECTION_TYPES = {
        '6': 'wired',
        '23': 'VPN',
        '71': 'wireless',
        '243': 'mobile',
    }

    def get_profiling_from_registry(self, hive_system, hive_software, hive_sam):
        profiling = {}
        reg_system = RegistryHive(hive_system)
        reg_software = RegistryHive(hive_software)
        reg_sam = RegistryHive(hive_sam)

        profiling['control_sets'] = self.__get_control_sets(reg_system)
        current_control_set = '\\ControlSet{:03d}'.format(profiling['control_sets']['current'])

        profiling['computer_name'] = self.__get_computer_name(reg_system, current_control_set)
        profiling['os'] = self.__get_operating_system(reg_software)
        profiling['time_zone'] = self.__get_timezone(reg_system, current_control_set)
        profiling['networks'] = self.__get_networks(reg_system, reg_software, current_control_set)
        profiling['local_users'] = self.__get_local_users(reg_sam)

        # usb
        # installed/uninstalled application (includes well known RAT and cloud)
        # startup keys
        # mapping device/guid/drive letter
        return profiling

    def __get_control_sets(self, reg_system):
        key = reg_system.get_key('\\Select')
        values = {value.name: value.value for value in key.get_values()}

        return {
            'current': values['Current'],
            'last_known_good': values['LastKnownGood'],
            'available': reg_system.get_control_sets(''),
        }

    def __get_computer_name(self, reg_system, current_control_set):
        path = current_control_set + '\\Control\\ComputerName\\ComputerName'
        key = reg_system.get_key(path)
        values = {value.name: value.value for value in key.get_values()}

        return values['ComputerName']

    def __get_operating_system(self, reg_software):
        path = '\\Microsoft\\Windows NT\\CurrentVersion'
        key = reg_software.get_key(path)
        values = {value.name: value.value for value in key.get_values()}
        version = '{} Release {} Build {}'.format(values['ProductName'], values['ReleaseId'], values['CurrentBuild'])

        return {
            'version': version,
            'install_date': self._unixepoch_to_datetime(values['InstallDate']),
        }

    def __get_timezone(self, reg_system, current_control_set):
        path = current_control_set + '\\Control\\TimeZoneInformation'
        key = reg_system.get_key(path)
        values = {value.name: value.value for value in key.get_values()}

        return {
            'name': values['TimeZoneKeyName'],
            'active_time_bias': values['ActiveTimeBias'],
        }

    def __get_networks(self, reg_system, reg_software, current_control_set):
        networks = {
            'nics': [],
            'parameters': [],
            'connections': [],
        }

        # collect NIC
        path = '\\Microsoft\\Windows NT\\CurrentVersion\\NetworkCards'
        key = reg_software.get_key(path)
        for subkey in key.iter_subkeys():
            values = {value.name: value.value for value in subkey.get_values()}
            networks['nics'].append({
                'guid': values['ServiceName'],
                'description': values['Description'],
            })

        # collect last known parameters (IP, subnet, domain, DHCP, NS, ...)
        for nic in networks['nics']:
            path = current_control_set + '\\Services\\Tcpip\\Parameters\\Interfaces\\' + nic['guid'].lower()
            key = reg_system.get_key(path)

            # main key for a NIC
            parameters = self.__decode_tcpip_interface_key(nic, key)
            if parameters is not None:
                networks['parameters'].append(parameters)

            # subkeys for WiFi access points
            for subkey in key.iter_subkeys():
                subparameters = self.__decode_tcpip_interface_key(nic, subkey)
                if subparameters is not None:
                    networks['parameters'].append(subparameters)

        # collect connections
        parameters_indexed = {parameters['network_hint']: parameters for parameters in networks['parameters']}
        subkeys = ['Managed', 'Unmanaged']
        for sk in subkeys:
            path = '\\Microsoft\\Windows NT\\CurrentVersion\\NetworkList\\Signatures\\' + sk
            key = reg_software.get_key(path)
            for subkey in key.iter_subkeys():
                values_signature = {value.name: value.value for value in subkey.get_values()}
                profile = reg_software.get_key('\\Microsoft\\Windows NT\\CurrentVersion\\NetworkList\\Profiles\\' + values_signature['ProfileGuid'])
                values_profile = {value.name: value.value for value in profile.get_values()}

                # attempt to match an IP based on SSID
                ssid = values_profile['ProfileName']
                ip_first = None
                ip_last = None
                first_connected_at = self._systemtime_to_datetime(values_profile['DateCreated'])
                last_connected_at = self._systemtime_to_datetime(values_profile['DateLastConnected'])
                parameters = parameters_indexed.get(ssid)
                if parameters is not None:
                    lease_start = parameters['lease_start']
                    lease_end = parameters['lease_end']
                    if first_connected_at >= parameters['lease_start'] and first_connected_at <= parameters['lease_end']:
                        ip_first = parameters['ip']
                    if last_connected_at >= parameters['lease_start'] and last_connected_at <= parameters['lease_end']:
                        ip_last = parameters['ip']

                # record the connection
                networks['connections'].append({
                    'gateway_mac': bytes(values_signature['DefaultGatewayMac']).hex(),
                    'dns_suffix': values_signature['DnsSuffix'],
                    'ssid': ssid,
                    'profile_guid': values_signature['ProfileGuid'],
                    'connection_type': self.__CONNECTION_TYPES[str(values_profile['NameType'])],
                    'first_connected_at': first_connected_at,
                    'last_connected_at': last_connected_at,
                    'ip_first': ip_first,
                    'ip_last': ip_last,
                })

        return networks

    def __decode_tcpip_interface_key(self, nic, key):
        values = {value.name: value.value for value in key.get_values()}

        if values.get('DhcpIPAddress') is None:
            return None

        network_hint = ''
        if values.get('DhcpIPAddress') is not None:
            for idx in range(0, len(values['DhcpNetworkHint']) - 1, 2):
                network_hint += values['DhcpNetworkHint'][idx + 1] + values['DhcpNetworkHint'][idx]

        domain = ''
        if values.get('DhcpDomain') is not None:
            domain = values['DhcpDomain']

        return {
            'nic_guid': nic['guid'],
            'network_hint': bytes.fromhex(network_hint).decode('utf-8'),
            'ip': values['DhcpIPAddress'],
            'subnet_mask': values['DhcpSubnetMask'],
            'dhcp_server': values['DhcpServer'],
            'dns_servers': values['DhcpNameServer'],
            'gateway': ','.join(values['DhcpDefaultGateway']),
            'domain': domain,
            'last_lease_start': self._unixepoch_to_datetime(values['LeaseObtainedTime']),
            'last_lease_end': self._unixepoch_to_datetime(values['LeaseTerminatesTime']),
        }

    def __get_local_users(self, reg_sam):
        # parsing based on https://github.com/EricZimmerman/RegistryPlugins: UserAccounts.cs
        users = []

        # groups -> builtin> aliases
        # collect local accounts creation date
        accounts_creation = {}
        path = '\\SAM\\Domains\\Account\\Users\\Names'
        key = reg_sam.get_key(path)
        for subkey in key.iter_subkeys():
            accounts_creation[subkey.header.key_name_string.decode('utf8')] = self._filetime_to_datetime(subkey.header.last_modified)

        # collect group membership info
        group_members = {}
        path = '\\SAM\\Domains\\Builtin\\Aliases'
        key = reg_sam.get_key(path)
        for subkey in key.iter_subkeys():
            if subkey.header.key_name_string in [b'Names', b'Members']:
                continue

            value = subkey.get_value('C')

            base_offset = 0x34
            offset = int.from_bytes(value[0x10:0x14], byteorder='little', signed=False) + base_offset
            length = int.from_bytes(value[0x14:0x18], byteorder='little', signed=False)
            group_name = value[offset:offset + length].decode('utf-16le')

            offset = int.from_bytes(value[0x28:0x2c], byteorder='little', signed=False) + base_offset
            nb_members = int.from_bytes(value[0x30:0x34], byteorder='little', signed=False)

            members = []
            offset_start = offset
            for i in range(0, nb_members):
                sid_type = int.from_bytes(value[offset_start:offset_start + 2], byteorder='little', signed=False)

                sid_bytes = None
                if sid_type == 0x501:
                    sid_bytes = value[offset_start:offset_start + 0x1c]
                    step = 0x1c

                if sid_type == 0x101:
                    sid_bytes = value[offset_start:offset_start + 0x0c]
                    step = 0x0c

                sid = ['S']
                sid.append(str(sid_bytes[0]))
                sid.append(str(int.from_bytes(sid_bytes[4:8], byteorder='big', signed=False)))
                for i in range(8, len(sid_bytes)-1, 4):
                    sid.append(str(int.from_bytes(sid_bytes[i:i+4], byteorder='little', signed=False)))

                members.append('-'.join(sid))
                offset_start += step

            group_members[group_name] = {
                'sids': members,
                'rids': [sid.split('-')[-1] for sid in members],
            }

        # collect account info
        path = '\\SAM\\Domains\\Account\\Users'

        key = reg_sam.get_key(path)
        for subkey in key.iter_subkeys():
            if subkey.header.key_name_string == b'Names':
                continue

            values = {value.name: value.value for value in subkey.get_values()}

            ms_account = ''
            if values.get('InternetUserName') is not None:
                ms_account += values['InternetUserName'].decode('utf-16le')

            rid = int.from_bytes(values['F'][0x30:0x34], byteorder='little', signed=False)
            membership = [name for name, members in group_members.items() if str(rid) in members['rids']]

            ft = int.from_bytes(values['F'][0x08:0x0B], byteorder='little', signed=False)
            last_login = self._filetime_to_datetime(ft)

            ft = int.from_bytes(values['F'][0x18:0x20], byteorder='little', signed=False)
            last_pw_change = self._filetime_to_datetime(ft)

            ft = int.from_bytes(values['F'][0x20:0x28], byteorder='little', signed=False)
            account_expire = self._filetime_to_datetime(ft)

            ft = int.from_bytes(values['F'][0x28:0x30], byteorder='little', signed=False)
            last_pw_incorrect = self._filetime_to_datetime(ft)

            account_disabled = False
            flags = int.from_bytes(values['F'][0x38:0x3a], byteorder='little', signed=False)
            if flags & 1 == 1:
                account_disabled = True

            base_offset = 0xcc
            offset = int.from_bytes(values['V'][0x0c:0x10], byteorder='little', signed=False) + base_offset
            length = int.from_bytes(values['V'][0x10:0x14], byteorder='little', signed=False)
            username = values['V'][offset:offset+length].decode('utf-16le')

            offset = int.from_bytes(values['V'][0x18:0x1c], byteorder='little', signed=False) + base_offset
            length = int.from_bytes(values['V'][0x1c:0x20], byteorder='little', signed=False)
            full_name = values['V'][offset:offset+length].decode('utf-16le')

            users.append({
                'rid': rid,
                'username': username,
                'full_name': full_name,
                'ms_account': ms_account,
                'groups_membership': ','.join(membership),
                'nb_logins_invalid': int.from_bytes(values['F'][0x40:0x42], byteorder='little', signed=False),
                'nb_logins_total': int.from_bytes(values['F'][0x42:0x44], byteorder='little', signed=False),
                'account_disabled': account_disabled,
                'account_created_at': str(accounts_creation[username]),
                'expire_at': str(account_expire) if account_expire is not None else '',
                'last_login_at': str(last_login) if last_login is not None else '',
                'last_pw_incorrect': str(last_pw_incorrect) if last_pw_incorrect is not None else '',
                'last_pw_change_at': str(last_pw_change) if last_pw_change is not None else '',
            })

        return users

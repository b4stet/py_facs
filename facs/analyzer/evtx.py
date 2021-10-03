from facs.entity.report import ReportEntity
import json
from xml.dom import minidom
import pyevtx
from facs.entity.timeline import TimelineEntity
from facs.analyzer.abstract import AbstractAnalyzer


class EvtxAnalyzer(AbstractAnalyzer):
    __CHANNELS_MIN = [
        'Security',
        'System',
        'Application',
        'Microsoft-Windows-TaskScheduler/Operational',
        'Microsoft-Windows-TerminalServices-RDPClient/Operational',
        'Microsoft-Windows-TerminalServices-RemoteConnectionManager/Operational',
        'Microsoft-Windows-TerminalServices-LocalSessionManager/Operational',
    ]

    def collect_profiling_events(self, fd_evtx):
        collection = {
            'app_uninstalled': [],
            'storage_info': [],
            'pnp_connections': [],
        }
        log_start_end = {channel: {'start': None, 'end': None} for channel in self.__CHANNELS_MIN}
        timeline = []
        computer_name = None
        report = {}

        nb_events = 0
        for line in fd_evtx:
            nb_events += 1
            if nb_events % 1000 == 0:
                print('.', end='', flush=True)

            event = json.loads(line)
            info = self.__extract_common(event['xml_string'])

            if info is None:
                continue

            channel = info['channel']
            provider = info['provider']
            event_id = info['event_id']

            if computer_name is None:
                computer_name = info['computer']

            # collect start/end of logs
            if log_start_end[channel]['start'] is None or info['datetime'] < log_start_end[channel]['start']:
                log_start_end[channel]['start'] = info['datetime']

            if log_start_end[channel]['end'] is None or info['datetime'] > log_start_end[channel]['end']:
                log_start_end[channel]['end'] = info['datetime']

            # check time changes, logging tampered and windows start/stop from Security channel
            if channel == 'Security':
                if provider == 'Microsoft-Windows-Security-Auditing' and event_id == '4616':
                    data = self.__extract_security_4616(event['xml_string'])
                    event_processed = self.__process_security_4616(info, data)
                    timeline = self._append_to_timeline(event_processed, timeline)

                if provider == 'Microsoft-Windows-Security-Auditing' and event_id in ['4608', '4609']:
                    event_processed = self.__process_security_4608_4609(info)
                    timeline = self._append_to_timeline(event_processed, timeline)

                if provider == 'Microsoft-Windows-Eventlog' and event_id in ['1100', '1102', '1104']:
                    data = self.__extract_security_1100_1102_1104(event['xml_string'])
                    event_processed = self.__process_security_1100_1102_1104(info, data)
                    timeline = self._append_to_timeline(event_processed, timeline)

            # check time changes, logging tampered and system start/stop/sleep/wake_up from System channel
            if channel == 'System':
                if provider == 'Microsoft-Windows-Kernel-General' and event_id == '1':
                    data = self.__extract_system_1(event['xml_string'])
                    event_processed = self.__process_system_1(info, data)
                    timeline = self._append_to_timeline(event_processed, timeline)

                if provider == 'Microsoft-Windows-Kernel-General' and event_id in ['12', '13']:
                    data = self.__extract_system_12_13(event['xml_string'])
                    event_processed = self.__process_system_12_13(info, data)
                    timeline = self._append_to_timeline(event_processed, timeline)

                if provider == 'Microsoft-Windows-Power-Troubleshooter' and event_id == '1':
                    data = self.__extract_system_power_1(event['xml_string'])
                    event_processed = self.__process_system_power_1(info, data)
                    timeline = self._append_to_timeline(event_processed, timeline)

                if provider == 'User32' and event_id == '1074':
                    data = self.__extract_system_1074(event['xml_string'])
                    event_processed = self.__process_system_1074(info, data)
                    timeline = self._append_to_timeline(event_processed, timeline)

                if provider == 'EventLog' and event_id in ['6005', '6006']:
                    event_processed = self.__process_system_6005_6006(info)
                    timeline = self._append_to_timeline(event_processed, timeline)

            # look for app_uninstalled applications
            if channel == 'Application':
                if provider == 'MsiInstaller' and event_id == '11724':
                    data = self.__extract_application_11724(event['xml_string'])
                    event_processed = self.__process_application_11724(info, data)
                    collection['app_uninstalled'] = self._append_to_timeline(event_processed, collection['app_uninstalled'])

            # collect info on storage devices (internal, external drives, USB MSC keys)
            if channel == 'Microsoft-Windows-Partition/Diagnostic':
                if provider == 'Microsoft-Windows-Partition' and event_id == '1006':
                    device = self.__extract_partition_1006(event['xml_string'])
                    if device is not None and device not in collection['storage_info']:
                        collection['storage_info'].append(device)

            # collect device connections to an USB port
            if channel == 'Microsoft-Windows-Kernel-PnP/Configuration':
                if provider == 'Microsoft-Windows-Kernel-PnP' and event_id in ['410', '430']:
                    data = self.__extract_kernel_pnp_410_430(event['xml_string'])
                    event_processed = self.__process_kernel_pnp_410_430(info, data)
                    collection['pnp_connections'] = self._append_to_timeline(event_processed, collection['pnp_connections'])

        # report if major evtx were found
        report['log_start_end'] = ReportEntity(
            title='Checked start/end of windows event log for main channels',
            details=[]
        )
        for channel in self.__CHANNELS_MIN:
            found = 'found'
            if log_start_end[channel]['start'] is None:
                found = 'not found'

            report['log_start_end'].details.append('{:80}: {}'.format(channel, found))

        # insert all evtx start/end in the timeline
        for channel in log_start_end:
            event = TimelineEntity(
                start=log_start_end[channel]['start'],
                end=log_start_end[channel]['end'],
                host=computer_name,
                event='log start/end',
                event_type=TimelineEntity.TIMELINE_TYPE_LOG,
                source='{}.evtx'.format(channel)
            )

            timeline.append(event.to_dict())

        # list what was done
        report['time_changed'] = ReportEntity(
            title='Checked evidences of system backdating',
            details=[
                'looked for clock drifts bigger than 10 minutes',
                'from Security channel, provider Microsoft-Windows-Security-Auditing, EID 4616 where user is not "LOCAL SERVICE" or "SYSTEM"',
                'from System channel, provider Microsoft-Windows-Kernel-General, EID 1 where reason is not 2',
            ]
        )

        report['log_tampered'] = ReportEntity(
            title='Checked evidences of log tampering',
            details=[
                'from Security channel, provider Microsoft-Windows-Eventlog, EID 1100/1102/1104',
                'from System channel, provider Eventlog, EID 6005/6006',
            ]
        )

        report['host_start_stop'] = ReportEntity(
            title='Checked evidences of host start/stop/sleep/wake up',
            details=[
                'from Security channel, provider Microsoft-Windows-Eventlog, EID 4608/4609',
                'from System channel, provider Microsoft-Windows-Kernel-General, EID 12/13',
                'from System channel, provider Microsoft-Windows-Power-Troubleshooter, EID 1',
                'from System channel, provider User32, EID 1074',
            ]
        )

        return nb_events, report, timeline, collection

    def __extract_common(self, evtx_xml):
        try:
            # because plaso create sometimes but rarely invalid xml string
            event = minidom.parseString(evtx_xml)
        except Exception:
            return None

        system = event.getElementsByTagName('System')[0]
        info = {
            'datetime': self._isoformat_to_datetime(system.getElementsByTagName('TimeCreated')[0].getAttribute('SystemTime')),
            'channel': system.getElementsByTagName('Channel')[0].firstChild.data,
            'provider': system.getElementsByTagName('Provider')[0].getAttribute('Name'),
            'event_id': system.getElementsByTagName('EventID')[0].firstChild.data,
            'computer': system.getElementsByTagName('Computer')[0].firstChild.data,
            'sid': system.getElementsByTagName('Security')[0].getAttribute('UserID'),
        }

        return info

    def extract_generic(self, evtx_file):
        evtx = pyevtx.file()
        evtx.open(evtx_file)

        nb_events = evtx.get_number_of_records()
        if nb_events == 0:
            return 0, 0, None

        events = []
        nb_dropped = 0
        for record in evtx.records:
            try:
                xml = record.get_xml_string()
                dom = minidom.parseString(xml)
            except Exception:
                # when xmlns is missing, an error is raised
                nb_dropped += 1
                continue

            event = {'raw': xml}

            # system info
            event.update(self.__parse_common_data(dom))

            # specific info
            if len(dom.getElementsByTagName('EventData')) > 0:
                parsed = self.__parse_event_data(dom)
                if parsed is not None:
                    event.update(parsed)
            elif len(dom.getElementsByTagName('ProcessingErrorData')) > 0:
                event.update(self.__parse_error_data(dom))
            elif len(dom.getElementsByTagName('UserData')) > 0:
                parsed = self.__parse_user_data(dom)
                if parsed is not None:
                    event.update(parsed)

            # enrich with tags
            event = self.__enrich(event)

            events.append(event)

        evtx.close()

        return nb_events, nb_dropped, events

    def __parse_common_data(self, dom):
        return {
            'datetime': str(self._isoformat_to_datetime(dom.getElementsByTagName('TimeCreated')[0].getAttribute('SystemTime'))),
            'channel': dom.getElementsByTagName('Channel')[0].firstChild.data,
            'provider': dom.getElementsByTagName('Provider')[0].getAttribute('Name'),
            'eid': dom.getElementsByTagName('EventID')[0].firstChild.data,
            'computer': dom.getElementsByTagName('Computer')[0].firstChild.data,
            'writer_sid': dom.getElementsByTagName('Security')[0].getAttribute('UserID'),
        }

    def __parse_event_data(self, dom):
        data = dom.getElementsByTagName('EventData')[0]
        event_data = {
            'misc': [],
        }
        for child in data.childNodes:
            if child.nodeType != minidom.Node.ELEMENT_NODE:
                continue

            if child.tagName == 'Data':
                if child.hasAttribute('Name') is True:
                    event_data[child.getAttribute('Name')] = child.firstChild.data if child.firstChild is not None else ''
                else:
                    event_data['misc'].append(child.firstChild.data if child.firstChild is not None else '')
            else:
                event_data[child.tagName] = child.firstChild.data if child.firstChild is not None else ''

        event_data['misc'] = ';'.join(event_data['misc'])
        if event_data['misc'] == '':
            del event_data['misc']

        if len(event_data) == 0:
            return None

        return event_data

    def __parse_error_data(self, dom):
        data = dom.getElementsByTagName('ProcessingErrorData')[0]
        return {
            'error_code': data.getElementsByTagName('ErrorCode')[0].firstChild.data,
            'item_name':  data.getElementsByTagName('DataItemName')[0].firstChild.data,
            'payload':  data.getElementsByTagName('EventPayload')[0].firstChild.data,
        }

    def __parse_user_data(self, dom):
        data = dom.getElementsByTagName('UserData')[0]
        event = {}

        content = data.firstChild
        if content.hasChildNodes() is False:
            return None

        for child in content.childNodes:
            if child.nodeType != minidom.Node.ELEMENT_NODE:
                continue

            if child.hasChildNodes() is False:
                event[child.tagName] = ''
            if child.hasChildNodes() is True and child.firstChild.nodeType == minidom.Node.TEXT_NODE:
                event[child.tagName] = child.firstChild.data
            if child.hasChildNodes() is True and child.firstChild.nodeType == minidom.Node.ELEMENT_NODE:
                elts = []
                for subchild in child.childNodes:
                    text = subchild.firstChild.data if subchild.firstChild is not None else ''
                    elts.append(text)
                event[child.tagName] = ';'.join([elt for elt in elts if elt != ''])

        return event

    def __enrich(self, event):
        event['timestamp'] = self._isoformat_to_unixepoch(event['datetime'])
        event['source'] = 'log_evtx'

        # add tags for known events
        event['tags'] = []

        if event['channel'] == 'Security' and event['provider'] == 'Microsoft-Windows-Security-Auditing' and event['eid'] in ['4608', '4609']:
            event['tags'].append('os_start_stop')

        if event['channel'] == 'Security' and event['provider'] == 'Microsoft-Windows-Eventlog' and event['eid'] in ['1100', '1102', '1104']:
            event['tags'].append('logging_altered')

        if event['channel'] == 'Security' and event['provider'] == 'Microsoft-Windows-Security-Auditing' and event['eid'] in ['4624', '4625', '4648']:
            event['tags'].append('authn')

        if event['channel'] == 'Security' and event['provider'] == 'Microsoft-Windows-Security-Auditing' and event['eid'] in ['4634', '4647']:
            event['tags'].append('logoff')

        if event['channel'] == 'Security' and event['provider'] == 'Microsoft-Windows-Security-Auditing' and event['eid'] in ['4672', '4964']:
            event['tags'].append('authn_privileged')

        if event['channel'] == 'Security' and event['provider'] == 'Microsoft-Windows-Security-Auditing' and event['eid'] in ['4768', '4771', '4772']:
            event['tags'].extend(['dc', 'authn_domain_kerberos', 'tgt_request'])

        if event['channel'] == 'Security' and event['provider'] == 'Microsoft-Windows-Security-Auditing' and event['eid'] in ['4769', '4770', '4773']:
            event['tags'].extend(['dc', 'authz_domain_kerberos', 'tgs_request'])

        if event['channel'] == 'Security' and event['provider'] == 'Microsoft-Windows-Security-Auditing' and event['eid'] in ['4776', '4777']:
            event['tags'].extend(['dc', 'authn_domain_ntlm'])

        if event['channel'] == 'Security' and event['provider'] == 'Microsoft-Windows-Security-Auditing' and event['eid'] in ['4825', '4778', '4779']:
            event['tags'].extend(['rdp_incoming'])

        if event['channel'] == 'Security' and event['provider'] == 'Microsoft-Windows-Security-Auditing' and event['eid'] == '4720':
            event['tags'].extend('user_new')

        if event['channel'] == 'Security' and event['provider'] == 'Microsoft-Windows-Security-Auditing' and event['eid'] in ['4728', '4732', '4756']:
            event['tags'].extend(['user_groups_modified'])

        if event['channel'] == 'Security' and event['provider'] == 'Microsoft-Windows-Security-Auditing' and event['eid'] in ['4798', '4799']:
            event['tags'].extend(['user_groups_enumeration'])

        if event['channel'] == 'Security' and event['provider'] == 'Microsoft-Windows-Security-Auditing' and event['eid'] in ['5140', '5141', '5142', '5143', '5144', '5145']:
            event['tags'].extend(['network_share_access'])

        if event['channel'] == 'Security' and event['provider'] == 'Microsoft-Windows-Security-Auditing' and event['eid'] in ['4688', '4689']:
            event['tags'].extend(['process_execution'])

        if event['channel'] == 'Security' and event['provider'] == 'Microsoft-Windows-Security-Auditing' and event['eid'] == '4697':
            event['tags'].extend(['service_new'])

        if event['channel'] == 'Security' and event['provider'] == 'Microsoft-Windows-Security-Auditing' and event['eid'] == '4698':
            event['tags'].extend(['scheduled_jobs_new'])

        if event['channel'] == 'Security' and event['provider'] == 'Microsoft-Windows-Security-Auditing' and event['eid'] in ['5024', '5025']:
            event['tags'].extend(['local_firewall_start_stop'])

        if event['channel'] == 'Security' and event['provider'] == 'Microsoft-Windows-Security-Auditing' and event['eid'] in ['5156', '5157']:
            event['tags'].extend(['network_connection_allowed_blocked'])

        if event['channel'] == 'Security' and event['provider'] == 'Microsoft-Windows-Security-Auditing' and event['eid'] == '6416':
            event['tags'].extend(['external_device_new'])

        if event['channel'] == 'Security' and event['provider'] == 'Microsoft-Windows-Security-Auditing' and event['eid'] == '4693':
            event['tags'].extend(['dpapi_key_recovery'])

        if event['channel'] == 'Security' and event['provider'] == 'Microsoft-Windows-Security-Auditing' and event['eid'] in ['4932', '4933']:
            event['tags'].extend(['dc', 'dc_replication_start_stop'])

        if event['channel'] == 'Security' and event['provider'] == 'Microsoft-Windows-Security-Auditing' and event['eid'] == '4657':
            event['tags'].extend(['reg_key_modified'])

        if event['channel'] == 'Security' and event['provider'] == 'Microsoft-Windows-Security-Auditing' and event['eid'] == '6416':
            event['tags'].extend(['external_device_new'])

        if event['channel'] == 'System' and event['provider'] == 'Microsoft-Windows-Kernel-General' and event['eid'] in ['12', '13']:
            event['tags'].extend(['system_start_stop'])

        if event['channel'] == 'System' and event['provider'] == 'Service Control Manager' and event['eid'] == '7045':
            event['tags'].extend(['service_new'])

        if event['channel'] == 'System' and event['provider'] == 'Service Control Manager' and event['eid'] in ['7034', '7035', '7040']:
            event['tags'].extend(['service_start_stop'])

        if event['channel'] == 'Microsoft-Windows-TaskScheduler/Operational' and event['eid'] == '106':
            event['tags'].extend(['scheduled_jobs_new'])

        if event['channel'] == 'Microsoft-Windows-TaskScheduler/Operational' and event['eid'] in ['200', '201']:
            event['tags'].extend(['scheduled_jobs_execution'])

        if event['channel'] == 'Microsoft-Windows-TerminalServices-RDPClient/Operational' and event['eid'] in ['1024', '1029', '1102']:
            event['tags'].extend(['rdp_outgoing'])

        if event['channel'] == 'Microsoft-Windows-RemoteDesktopServices-RdpCoreTS/Operational' and event['eid'] == '131':
            event['tags'].extend(['rdp_incoming'])

        if event['channel'] == 'Microsoft-Windows-TerminalServices-RemoteConnectionManager/Operational' and event['eid'] == '1149':
            event['tags'].extend(['rdp_incoming'])

        if event['channel'] == 'Microsoft-Windows-TerminalServices-LocalSessionManager/Operational' and event['eid'] in ['21', '22', '24', '25']:
            event['tags'].extend(['rdp_incoming'])

        if event['channel'] == 'Microsoft-Windows-WinRM/Operational' and event['eid'] == '6':
            event['tags'].extend(['winrm_source_execution'])

        if event['channel'] == 'Microsoft-Windows-WinRM/Operational' and event['eid'] == '169':
            event['tags'].extend(['winrm_destination_execution'])

        if event['channel'] == 'Windows Powershell' and event['eid'] in ['400', '403']:
            event['tags'].extend(['powershell_start_stop'])

        if event['channel'] == 'Microsoft-Windows-PowerShell/Operational' and event['eid'] in ['4013', '4104']:
            event['tags'].extend(['powershell_execution'])

        if event['channel'] == 'Microsoft-Windows-Shell-Core/Operational' and event['eid'] in ['9707', '9708']:
            event['tags'].extend(['reg_runkey_execution'])

        if event['channel'] == 'Microsoft-Windows-Bits-Client/Operational' and event['eid'] == '59':
            event['tags'].extend(['bits_download_upload'])

        if event['channel'] == 'Microsoft-Windows-DNS-Client/Operational' and event['eid'] == '3006':
            event['tags'].append('dns_query')

        if event['channel'] == 'Microsoft-Windows-DriverFrameworks-UserMode/Operational' and event['eid'] in ['2101', '2102']:
            event['tags'].extend(['external_device_connection'])

        if event['channel'] == 'Microsoft-Windows-MBAM/Operational' and event['eid'] in ['39', '40']:
            event['tags'].extend(['external_device_mounting'])

        if event['channel'] == 'OAlerts' and event['eid'] == '300':
            event['tags'].extend(['graphical', 'office_doc_access'])

        # catch all
        if len(event['tags']) == 0:
            event['tags'] = 'no_tags'

        return event

    def __extract_security_4616(self, evtx_xml):
        event = minidom.parseString(evtx_xml)
        data = event.getElementsByTagName('EventData')[0].getElementsByTagName('Data')
        info = {}
        for elt in data:
            attribute = elt.getAttribute('Name')
            if attribute == 'SubjectUserSid':
                info['sid'] = elt.firstChild.data

            if attribute == 'SubjectUserName':
                info['username'] = elt.firstChild.data

            if attribute == 'SubjectDomainName':
                info['domain'] = elt.firstChild.data

            if attribute == 'PreviousTime':
                info['previous_time'] = self._isoformat_to_datetime(elt.firstChild.data)

            if attribute == 'NewTime':
                info['current_time'] = self._isoformat_to_datetime(elt.firstChild.data)

            if attribute == 'ProcessName':
                info['process'] = elt.firstChild.data

        return info

    def __process_security_4616(self, info, data):
        keep_it = True

        # discard legitimate clock drift (NTP sync)
        if data['username'] in ['LOCAL SERVICE', 'SYSTEM']:
            keep_it = False

        # discard minor clock drift (10 min)
        delta = data['current_time'] - data['previous_time']
        if delta.total_seconds() < 600:
            keep_it = False

        if keep_it is False:
            return None

        user = '{}\\{} (SID {})'.format(data['domain'], data['username'], data['sid'])
        note = 'before {} ; after {} ; process {}'.format(str(data['previous_time']), str(data['current_time']), data['process'])
        source = 'EID {}; channel {} ; provider {}'.format(info['event_id'], info['channel'], info['provider'])

        return TimelineEntity(
            start=info['datetime'],
            host=info['computer'],
            user=user,
            event='system time changed',
            event_type=TimelineEntity.TIMELINE_TYPE_EVENT,
            source=source,
            note=note
        )

    def __extract_security_1100_1102_1104(self, evtx_xml):
        event = minidom.parseString(evtx_xml)
        data = event.getElementsByTagName('UserData')[0]
        info = {}

        # EID 1100
        shutdown = data.getElementsByTagName('ServiceShutdown')
        if len(shutdown) > 0:
            info['event'] = 'event logging service was shut down'

        # EID 1104
        full = data.getElementsByTagName('FileIsFull')
        if len(full) > 0:
            info['event'] = 'security log is full'

        # EID 1102
        cleared = data.getElementsByTagName('LogFileCleared')
        if len(cleared) > 0:
            info['event'] = 'security log was cleared'
            info['sid'] = cleared[0].getElementsByTagName('SubjectUserSid').firstChild.data
            info['username'] = cleared[0].getElementsByTagName('SubjectUserName').firstChild.data
            info['domain'] = cleared[0].getElementsByTagName('SubjectDomainName').firstChild.data

        return info

    def __process_security_1100_1102_1104(self, info, data):
        user = info['sid']
        if 'sid' in data.keys():
            user = '{}\\{} (SID {})'.format(data['domain'], data['username'], data['sid'])
        source = 'EID {}; channel {} ; provider {}'.format(info['event_id'], info['channel'], info['provider'])

        return TimelineEntity(
            start=info['datetime'],
            host=info['computer'],
            user=user,
            event=data['event'],
            event_type=TimelineEntity.TIMELINE_TYPE_EVENT,
            source=source
        )

    def __process_security_4608_4609(self, info):
        event = ''
        if info['event_id'] == '4608':
            event = 'Windows is starting up'

        if info['event_id'] == '4609':
            event = 'Windows is shutting down'

        source = 'EID {}; channel {} ; provider {}'.format(info['event_id'], info['channel'], info['provider'])

        return TimelineEntity(
            start=info['datetime'],
            host=info['computer'],
            user=info['sid'],
            event=event,
            event_type=TimelineEntity.TIMELINE_TYPE_EVENT,
            source=source
        )

    def __extract_system_1(self, evtx_xml):
        event = minidom.parseString(evtx_xml)
        data = event.getElementsByTagName('EventData')[0].getElementsByTagName('Data')
        info = {}
        for elt in data:
            attribute = elt.getAttribute('Name')
            if attribute == 'OldTime':
                info['previous_time'] = self._isoformat_to_datetime(elt.firstChild.data)

            if attribute == 'NewTime':
                info['current_time'] = self._isoformat_to_datetime(elt.firstChild.data)

            if attribute == 'Reason':
                info['reason'] = elt.firstChild.data

        return info

    def __process_system_1(self, info, data):
        keep_it = True

        # discard legitimate clock drift (2=System time synchronized with the hardware clock)
        if data['reason'] == '2':
            keep_it = False

        # discard minor clock drift (10 min)
        delta = data['current_time'] - data['previous_time']
        if delta.total_seconds() < 600:
            keep_it = False

        if keep_it is False:
            return None

        note = 'before {} ; after {} ; reason {}'.format(str(data['previous_time']), str(data['current_time']), data['reason'])
        source = 'EID {}; channel {} ; provider {}'.format(info['event_id'], info['channel'], info['provider'])

        return TimelineEntity(
            start=info['datetime'],
            host=info['computer'],
            user=info['sid'],
            event='system time changed',
            event_type=TimelineEntity.TIMELINE_TYPE_EVENT,
            source=source,
            note=note
        )

    def __extract_system_power_1(self, evtx_xml):
        event = minidom.parseString(evtx_xml)
        data = event.getElementsByTagName('EventData')[0].getElementsByTagName('Data')
        info = {}
        for elt in data:
            attribute = elt.getAttribute('Name')
            if attribute == 'SleepTime':
                info['sleep_start'] = self._isoformat_to_datetime(elt.firstChild.data)

            if attribute == 'WakeTime':
                info['sleep_end'] = self._isoformat_to_datetime(elt.firstChild.data)

        return info

    def __process_system_power_1(self, info, data):
        source = 'EID {}; channel {} ; provider {}'.format(info['event_id'], info['channel'], info['provider'])

        return TimelineEntity(
            start=data['sleep_start'],
            end=data['sleep_end'],
            host=info['computer'],
            user=info['sid'],
            event='sleeping time',
            event_type=TimelineEntity.TIMELINE_TYPE_EVENT,
            source=source,
        )

    def __extract_system_12_13(self, evtx_xml):
        event = minidom.parseString(evtx_xml)
        data = event.getElementsByTagName('EventData')[0].getElementsByTagName('Data')
        info = {}
        for elt in data:
            attribute = elt.getAttribute('Name')
            if attribute == 'StopTime':
                info['event'] = 'system stopped'
                info['time'] = self._isoformat_to_datetime(elt.firstChild.data)

            if attribute == 'StartTime':
                info['event'] = 'system started'
                info['time'] = self._isoformat_to_datetime(elt.firstChild.data)

        return info

    def __process_system_12_13(self, info, data):
        source = 'EID {}; channel {} ; provider {}'.format(info['event_id'], info['channel'], info['provider'])

        note = ''
        if info['event_id'] == '12':
            note = 'start time: '
        if info['event_id'] == '13':
            note = 'stop time: '
        note += str(data['time'])

        return TimelineEntity(
            start=info['datetime'],
            host=info['computer'],
            user=info['sid'],
            event=data['event'],
            event_type=TimelineEntity.TIMELINE_TYPE_EVENT,
            source=source,
            note=note
        )

    def __extract_system_1074(self, evtx_xml):
        event = minidom.parseString(evtx_xml)
        data = event.getElementsByTagName('EventData')[0].getElementsByTagName('Data')
        info = {}
        for elt in data:
            attribute = elt.getAttribute('Name')
            if attribute == 'param1':
                info['process'] = elt.firstChild.data

            if attribute == 'param3':
                info['reason'] = elt.firstChild.data

            if attribute == 'param4':
                info['reason'] += '(code {})'.format(elt.firstChild.data)

            if attribute == 'param5':
                info['event'] = elt.firstChild.data

            if attribute == 'param7':
                info['user'] = elt.firstChild.data
        return info

    def __process_system_1074(self, info, data):
        source = 'EID {}; channel {} ; provider {}'.format(info['event_id'], info['channel'], info['provider'])
        note = 'reason: {}, process: {}'.format(data['reason'], data['process'])

        return TimelineEntity(
            start=info['datetime'],
            host=info['computer'],
            user=data['user'],
            event=data['event'],
            event_type=TimelineEntity.TIMELINE_TYPE_EVENT,
            source=source,
            note=note
        )

    def __process_system_6005_6006(self, info):
        source = 'EID {}; channel {} ; provider {}'.format(info['event_id'], info['channel'], info['provider'])

        event = 'event log service '
        if info['event_id'] == '6005':
            event += 'started'

        if info['event_id'] == '6006':
            event += 'stopped'

        return TimelineEntity(
            start=info['datetime'],
            host=info['computer'],
            user=info['sid'],
            event=event,
            event_type=TimelineEntity.TIMELINE_TYPE_EVENT,
            source=source
        )

    def __extract_application_11724(self, evtx_xml):
        event = minidom.parseString(evtx_xml)
        data = event.getElementsByTagName('EventData')[0].getElementsByTagName('Data')[0]
        return {
            'event': 'application successfully removed',
            'product': data.firstChild.data,
        }

    def __process_application_11724(self, info, data):
        source = 'EID {}; channel {} ; provider {}'.format(info['event_id'], info['channel'], info['provider'])

        return TimelineEntity(
            start=info['datetime'],
            host=info['computer'],
            user=info['sid'],
            event=data['event'],
            event_type=TimelineEntity.TIMELINE_TYPE_EVENT,
            source=source,
            note=data['product']
        )

    def __extract_partition_1006(self, evtx_xml):
        event = minidom.parseString(evtx_xml)
        data = event.getElementsByTagName('EventData')[0].getElementsByTagName('Data')
        info = {}

        for elt in data:
            attribute = elt.getAttribute('Name')

            # when capacity is zero, it is just an unplug
            if info.get('bytes_capacity') is not None and info['bytes_capacity'] == 0:
                return None

            if attribute == 'Capacity' and elt.firstChild is not None:
                info['bytes_capacity'] = elt.firstChild.data

            if attribute == 'Manufacturer' and elt.firstChild is not None:
                info['manufacturer'] = elt.firstChild.data

            if attribute == 'Model' and elt.firstChild is not None:
                info['model'] = elt.firstChild.data

            if attribute == 'Revision' and elt.firstChild is not None:
                info['revision'] = elt.firstChild.data

            if attribute == 'SerialNumber' and elt.firstChild is not None:
                info['disk_serial_number'] = elt.firstChild.data

            if attribute == 'ParentId':
                parent_id = elt.firstChild.data.split('\\')
                if parent_id[0] == 'PCI':
                    info['vendor_product'] = parent_id[1]

                if parent_id[0] == 'USB':
                    info['vid_pid'] = parent_id[1]
                info['serial_number'] = parent_id[2]

            if attribute == 'DiskId':
                info['disk_guid'] = elt.firstChild.data

            if attribute == 'AdapterId':
                info['adapter_guid'] = elt.firstChild.data

            if attribute == 'RegistryId':
                info['registry_guid'] = elt.firstChild.data

            if attribute == 'PartitionTable' and elt.firstChild is not None:
                table = elt.firstChild.data
                partition_type = table[0:8]
                if partition_type == '00000000':
                    info['partition_type'] = self._PARTITION_MBR
                    info['disk_signature'] = table[16:24].lower()

                if partition_type == '01000000':
                    info['partition_type'] = self._PARTITION_GPT
                    info['partitions_guid'] = []
                    for i in range(0, len(table)-1, 32):
                        # collect the partition GUID if its header is "Basic Data Partition"
                        if table[i:i+32].lower() == 'a2a0d0ebe5b9334487c068b6b72699c7':
                            info['partitions_guid'].append(table[i+32:i+64].lower())
        return info

    def __extract_kernel_pnp_410_430(self, evtx_xml):
        event = minidom.parseString(evtx_xml)
        data = event.getElementsByTagName('EventData')[0].getElementsByTagName('Data')
        info = {}

        for elt in data:
            attribute = elt.getAttribute('Name')

            if attribute == 'DeviceInstanceId' and elt.firstChild.data.startswith('USB\\'):
                # pattern is USB\VID_XXX&PID_YYY\<SN>
                instance_id = elt.firstChild.data.split('\\')
                info['vid_pid'] = instance_id[1]
                info['serial_number'] = instance_id[2]

        return info

    def __process_kernel_pnp_410_430(self, info, data):
        if len(data) == 0:
            return None

        source = 'EID {}; channel {} ; provider {}'.format(info['event_id'], info['channel'], info['provider'])
        note = '{}#{}'.format(data['vid_pid'], data['serial_number'])

        return TimelineEntity(
            start=info['datetime'],
            host=info['computer'],
            user=info['sid'],
            event='USB device started',
            event_type=TimelineEntity.TIMELINE_TYPE_EVENT,
            source=source,
            note=note
        )

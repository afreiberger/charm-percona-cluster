import sys

import mock

from test_utils import CharmTestCase

sys.modules['MySQLdb'] = mock.Mock()
# python-apt is not installed as part of test-requirements but is imported by
# some charmhelpers modules so create a fake import.
sys.modules['apt'] = mock.Mock()

with mock.patch('charmhelpers.contrib.hardening.harden.harden') as mock_dec:
    mock_dec.side_effect = (lambda *dargs, **dkwargs: lambda f:
                            lambda *args, **kwargs: f(*args, **kwargs))
    import percona_hooks as hooks

TO_PATCH = ['log', 'config',
            'get_db_helper',
            'relation_ids',
            'relation_set',
            'update_nrpe_config',
            'get_iface_for_address',
            'get_netmask_for_address',
            'is_bootstrapped',
            'is_sufficient_peers',
            'network_get_primary_address',
            'resolve_network_cidr',
            'unit_get',
            'resolve_hostname_to_ip',
            'is_clustered',
            'get_ipv6_addr',
            'get_hacluster_config',
            'update_dns_ha_resource_params']


class TestHARelation(CharmTestCase):
    def setUp(self):
        CharmTestCase.setUp(self, hooks, TO_PATCH)
        self.network_get_primary_address.side_effect = NotImplementedError

    def test_resources(self):
        self.relation_ids.return_value = ['ha:1']
        password = 'ubuntu'
        helper = mock.Mock()
        attrs = {'get_mysql_password.return_value': password}
        helper.configure_mock(**attrs)
        self.get_db_helper.return_value = helper
        self.get_netmask_for_address.return_value = None
        self.get_iface_for_address.return_value = None
        self.test_config.set('vip', '10.0.3.3')
        self.test_config.set('sst-password', password)
        self.get_hacluster_config.return_value = {
            'vip': '10.0.3.3',
            'ha-bindiface': 'eth0',
            'ha-mcastport': 5490,
        }

        def f(k):
            return self.test_config.get(k)

        self.config.side_effect = f
        hooks.ha_relation_joined()

        resources = {'res_mysql_vip': 'ocf:heartbeat:IPaddr2',
                     'res_mysql_monitor': 'ocf:percona:mysql_monitor'}
        resource_params = {'res_mysql_vip': ('params ip="10.0.3.3" '
                                             'cidr_netmask="24" '
                                             'nic="eth0"'),
                           'res_mysql_monitor':
                           hooks.RES_MONITOR_PARAMS % {'sstpass': 'ubuntu'}}
        groups = {'grp_percona_cluster': 'res_mysql_vip'}

        clones = {'cl_mysql_monitor': 'res_mysql_monitor meta interleave=true'}

        colocations = {'colo_percona_cluster': 'inf: grp_percona_cluster cl_mysql_monitor'}  # noqa

        locations = {'loc_percona_cluster':
                     'grp_percona_cluster rule inf: writable eq 1'}

        self.relation_set.assert_called_with(
            relation_id='ha:1', corosync_bindiface=f('ha-bindiface'),
            corosync_mcastport=f('ha-mcastport'), resources=resources,
            resource_params=resource_params, groups=groups,
            clones=clones, colocations=colocations, locations=locations)

    def test_resource_params_vip_cidr_iface_autodetection(self):
        """
        Auto-detected values for vip_cidr and vip_iface are used to configure
        VIPs, even when explicit config options are provided.
        """
        self.relation_ids.return_value = ['ha:1']
        helper = mock.Mock()
        self.get_db_helper.return_value = helper
        self.get_netmask_for_address.return_value = '20'
        self.get_iface_for_address.return_value = 'eth1'
        self.test_config.set('vip', '10.0.3.3')
        self.test_config.set('vip_cidr', '16')
        self.test_config.set('vip_iface', 'eth0')
        self.get_hacluster_config.return_value = {
            'vip': '10.0.3.3',
            'ha-bindiface': 'eth0',
            'ha-mcastport': 5490,
        }

        def f(k):
            return self.test_config.get(k)

        self.config.side_effect = f
        hooks.ha_relation_joined()

        resource_params = {'res_mysql_vip': ('params ip="10.0.3.3" '
                                             'cidr_netmask="20" '
                                             'nic="eth1"'),
                           'res_mysql_monitor':
                           hooks.RES_MONITOR_PARAMS % {'sstpass': 'None'}}

        call_args, call_kwargs = self.relation_set.call_args
        self.assertEqual(resource_params, call_kwargs['resource_params'])

    def test_resource_params_no_vip_cidr_iface_autodetection(self):
        """
        When autodetecting vip_cidr and vip_iface fails, values from
        vip_cidr and vip_iface config options are used instead.
        """
        self.relation_ids.return_value = ['ha:1']
        helper = mock.Mock()
        self.get_db_helper.return_value = helper
        self.get_netmask_for_address.return_value = None
        self.get_iface_for_address.return_value = None
        self.test_config.set('vip', '10.0.3.3')
        self.test_config.set('vip_cidr', '16')
        self.test_config.set('vip_iface', 'eth1')
        self.get_hacluster_config.return_value = {
            'vip': '10.0.3.3',
            'ha-bindiface': 'eth1',
            'ha-mcastport': 5490,
        }

        def f(k):
            return self.test_config.get(k)

        self.config.side_effect = f
        hooks.ha_relation_joined()

        resource_params = {'res_mysql_vip': ('params ip="10.0.3.3" '
                                             'cidr_netmask="16" '
                                             'nic="eth1"'),
                           'res_mysql_monitor':
                           hooks.RES_MONITOR_PARAMS % {'sstpass': 'None'}}

        call_args, call_kwargs = self.relation_set.call_args
        self.assertEqual(resource_params, call_kwargs['resource_params'])


class TestHostResolution(CharmTestCase):
    def setUp(self):
        CharmTestCase.setUp(self, hooks, TO_PATCH)
        self.network_get_primary_address.side_effect = NotImplementedError
        self.is_clustered.return_value = False
        self.config.side_effect = self.test_config.get
        self.test_config.set('prefer-ipv6', False)

    def test_get_db_host_defaults(self):
        '''
        Ensure that with nothing other than defaults private-address is used
        '''
        self.unit_get.return_value = 'mydbhost'
        self.resolve_hostname_to_ip.return_value = '10.0.0.2'
        self.assertEqual(hooks.get_db_host('myclient'), 'mydbhost')

    def test_get_db_host_network_spaces(self):
        '''
        Ensure that if the shared-db relation is bound, its bound address
        is used
        '''
        self.resolve_hostname_to_ip.return_value = '10.0.0.2'
        self.network_get_primary_address.side_effect = None
        self.network_get_primary_address.return_value = '192.168.20.2'
        self.assertEqual(hooks.get_db_host('myclient'), '192.168.20.2')
        self.network_get_primary_address.assert_called_with('shared-db')

    def test_get_db_host_network_spaces_clustered(self):
        '''
        Ensure that if the shared-db relation is bound and the unit is
        clustered, that the correct VIP is chosen
        '''
        self.resolve_hostname_to_ip.return_value = '10.0.0.2'
        self.is_clustered.return_value = True
        self.test_config.set('vip', '10.0.0.100 192.168.20.200')
        self.network_get_primary_address.side_effect = None
        self.network_get_primary_address.return_value = '192.168.20.2'
        self.resolve_network_cidr.return_value = '192.168.20.2/24'
        self.assertEqual(hooks.get_db_host('myclient'), '192.168.20.200')
        self.network_get_primary_address.assert_called_with('shared-db')


class TestNRPERelation(CharmTestCase):
    def setUp(self):
        patch_targets_nrpe = TO_PATCH[:]
        patch_targets_nrpe.remove("update_nrpe_config")
        patch_targets_nrpe.append("nrpe")
        patch_targets_nrpe.append("apt_install")
        CharmTestCase.setUp(self, hooks, patch_targets_nrpe)

    def test_mysql_monitored(self):
        """The mysql service is monitored by Nagios."""
        hooks.update_nrpe_config()
        self.nrpe.add_init_service_checks.assert_called_once_with(
            mock.ANY, ["mysql"], mock.ANY)

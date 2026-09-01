from gateway.transports.names import TransportName


def test_feishu_transport_name():
    assert TransportName.FEISHU == "feishu"

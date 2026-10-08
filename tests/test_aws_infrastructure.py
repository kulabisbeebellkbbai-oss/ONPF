"""Local graph/policy checks; these do not simulate AWS provisioning."""
import base64
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "deploy/aws/infrastructure.json"
EXAMPLES = ROOT / "deploy/aws/parameters.example.json"


@pytest.fixture
def template():
    assert TEMPLATE.exists(), "AWS infrastructure template has not been implemented"
    return json.loads(TEMPLATE.read_text(encoding="utf-8"))


class Graph:
    """Resolve the template's supported expressions into hand-selected identities."""

    def __init__(self, template, region="ca-central-1"):
        self.template = template
        self.values = {name: value.get("Default", "")
                       for name, value in template["Parameters"].items()}
        self.values.update({"AWS::Partition": "aws", "AWS::Region": region,
                            "AWS::AccountId": "123456789012", "AWS::StackName": "agency-onpf",
                            "AWS::NoValue": None, "UbuntuImageId": "ami-0123456789abcdef0",
                            "PublicHost": "onpf.example.org"})
        for name, resource in template["Resources"].items():
            kind = resource["Type"]
            self.values[name] = {"AWS::S3::Bucket": "agency-" + name.lower(),
                                 "AWS::EC2::Instance": "i-0123456789abcdef0",
                                 "AWS::EC2::Volume": "vol-0123456789abcdef0"}.get(kind, name)

    def resolve(self, value):
        if isinstance(value, list):
            return [self.resolve(item) for item in value]
        if not isinstance(value, dict):
            return value
        if "Ref" in value:
            return self.values[value["Ref"]]
        if "Fn::GetAtt" in value:
            name, attribute = value["Fn::GetAtt"]
            if attribute == "Arn":
                kind = self.template["Resources"][name]["Type"]
                if kind == "AWS::S3::Bucket":
                    return "arn:aws:s3:::" + self.values[name]
                return "arn:aws:iam::123456789012:role/" + name
            if attribute == "AvailabilityZone":
                return self.values["AWS::Region"] + "a"
            if attribute == "AllocationId":
                return "eipalloc-0123456789abcdef0"
            raise AssertionError("Unsupported attribute: " + attribute)
        if "Fn::Sub" in value:
            sub = value["Fn::Sub"]
            text, bindings = (sub, {}) if isinstance(sub, str) else sub

            def replacement(match):
                key = match.group(1)
                if key in bindings:
                    return str(self.resolve(bindings[key]))
                if "." in key:
                    return str(self.resolve({"Fn::GetAtt": key.split(".", 1)}))
                return str(self.values[key])

            return re.sub(r"\$\{([^}]+)\}", replacement, text)
        if "Fn::Base64" in value:
            return base64.b64encode(self.resolve(value["Fn::Base64"]).encode()).decode()
        if "Fn::Select" in value:
            index, items = value["Fn::Select"]
            return self.resolve(items)[int(index)]
        if "Fn::GetAZs" in value:
            return [self.values["AWS::Region"] + suffix for suffix in ("a", "b")]
        return {key: self.resolve(item) for key, item in value.items()}

    def only(self, kind):
        found = [(name, resource) for name, resource in self.template["Resources"].items()
                 if resource["Type"] == kind]
        assert len(found) == 1
        return found[0]

    def output(self, name):
        return self.resolve(self.template["Outputs"][name]["Value"])


def statements(graph):
    _, role = graph.only("AWS::IAM::Role")
    return [graph.resolve(statement) for policy in role["Properties"]["Policies"]
            for statement in policy["PolicyDocument"]["Statement"]]


def for_action(graph, action):
    return [statement for statement in statements(graph)
            if statement["Effect"] == "Allow" and action in statement["Action"]]


def test_dedicated_network_routes_instance_and_volume_to_same_zone(template):
    graph = Graph(template)
    vpc_name, _ = graph.only("AWS::EC2::VPC")
    subnet_name, subnet = graph.only("AWS::EC2::Subnet")
    route_table_name, route_table = graph.only("AWS::EC2::RouteTable")
    gateway_name, _ = graph.only("AWS::EC2::InternetGateway")
    _, attachment = graph.only("AWS::EC2::VPCGatewayAttachment")
    _, association = graph.only("AWS::EC2::SubnetRouteTableAssociation")
    _, route = graph.only("AWS::EC2::Route")
    instance_name, instance = graph.only("AWS::EC2::Instance")
    volume_name, volume = graph.only("AWS::EC2::Volume")
    _, disk_attachment = graph.only("AWS::EC2::VolumeAttachment")
    assert subnet["Properties"]["VpcId"] == {"Ref": vpc_name}
    assert route_table["Properties"]["VpcId"] == {"Ref": vpc_name}
    assert attachment["Properties"] == {"VpcId": {"Ref": vpc_name},
                                         "InternetGatewayId": {"Ref": gateway_name}}
    assert association["Properties"] == {"SubnetId": {"Ref": subnet_name},
                                          "RouteTableId": {"Ref": route_table_name}}
    assert route["Properties"]["GatewayId"] == {"Ref": gateway_name}
    assert route["Properties"]["RouteTableId"] == {"Ref": route_table_name}
    assert route["Properties"]["DestinationCidrBlock"] == "0.0.0.0/0"
    assert instance["Properties"]["SubnetId"] == {"Ref": subnet_name}
    assert graph.resolve(volume["Properties"]["AvailabilityZone"]) == graph.resolve(subnet["Properties"]["AvailabilityZone"])
    assert disk_attachment["Properties"]["VolumeId"] == {"Ref": volume_name}
    assert disk_attachment["Properties"]["InstanceId"] == {"Ref": instance_name}


def test_public_ingress_is_http_https_only_with_no_admin_or_backend_port(template):
    graph = Graph(template)
    group_name, group = graph.only("AWS::EC2::SecurityGroup")
    _, instance = graph.only("AWS::EC2::Instance")
    assert instance["Properties"]["SecurityGroupIds"] == [{"Ref": group_name}]
    rules = group["Properties"]["SecurityGroupIngress"]
    assert {(rule["IpProtocol"], rule["FromPort"], rule["ToPort"], rule["CidrIp"])
            for rule in rules} == {("tcp", 80, 80, "0.0.0.0/0"), ("tcp", 443, 443, "0.0.0.0/0")}
    assert len(rules) == 2
    assert not any(resource["Type"] == "AWS::EC2::SecurityGroupIngress"
                   for resource in template["Resources"].values())
    assert "KeyName" not in instance["Properties"]


def test_instance_uses_pinned_amd64_image_imdsv2_standard_credits_and_encrypted_root(template):
    graph = Graph(template)
    _, instance = graph.only("AWS::EC2::Instance")
    props = instance["Properties"]
    assert template["Parameters"]["UbuntuImageId"]["Type"] == "AWS::EC2::Image::Id"
    assert "Default" not in template["Parameters"]["UbuntuImageId"]
    assert props["ImageId"] == {"Ref": "UbuntuImageId"}
    allowed = template["Parameters"]["InstanceType"]["AllowedValues"]
    assert "t3.small" in allowed and all(re.fullmatch(r"t3a?\.(small|medium|large)", item) for item in allowed)
    assert props["CreditSpecification"]["CPUCredits"] == "standard"
    assert props["MetadataOptions"]["HttpTokens"] == "required"
    assert props["MetadataOptions"]["HttpPutResponseHopLimit"] == 1
    root = props["BlockDeviceMappings"]
    assert len(root) == 1 and root[0]["Ebs"]["Encrypted"] is True
    assert root[0]["Ebs"]["VolumeType"] == "gp3"
    assert props["PropagateTagsToVolumeOnCreation"] is True


def test_volume_is_retained_on_delete_and_replacement_without_launch_time_formatting(template):
    graph = Graph(template)
    _, volume = graph.only("AWS::EC2::Volume")
    assert volume["DeletionPolicy"] == volume["UpdateReplacePolicy"] == "Retain"
    assert volume["Properties"]["Encrypted"] is True
    assert volume["Properties"]["VolumeType"] == "gp3"
    _, instance = graph.only("AWS::EC2::Instance")
    bootstrap = base64.b64decode(graph.resolve(instance["Properties"]["UserData"])).decode()
    assert not re.search(r"\b(mkfs|mke2fs|wipefs|mount|git|wget)\b", bootstrap)
    assert "nvme1n1" not in bootstrap and "onpf.production" not in bootstrap
    assert "systemctl disable --now nginx" in bootstrap


def test_elastic_address_is_associated_with_application_instance(template):
    graph = Graph(template)
    address_name, address = graph.only("AWS::EC2::EIP")
    instance_name, _ = graph.only("AWS::EC2::Instance")
    _, association = graph.only("AWS::EC2::EIPAssociation")
    assert address["Properties"]["Domain"] == "vpc"
    assert association["Properties"]["AllocationId"] == {"Fn::GetAtt": [address_name, "AllocationId"]}
    assert association["Properties"]["InstanceId"] == {"Ref": instance_name}


@pytest.mark.parametrize("name", ["ArtifactBucket", "BackupBucket"])
def test_private_buckets_enforce_encryption_acl_ownership_tls_and_retention(template, name):
    graph = Graph(template)
    bucket = template["Resources"][name]
    props = bucket["Properties"]
    assert bucket["DeletionPolicy"] == bucket["UpdateReplacePolicy"] == "Retain"
    assert props["PublicAccessBlockConfiguration"] == {
        "BlockPublicAcls": True, "BlockPublicPolicy": True,
        "IgnorePublicAcls": True, "RestrictPublicBuckets": True}
    assert props["OwnershipControls"]["Rules"] == [{"ObjectOwnership": "BucketOwnerEnforced"}]
    assert props["BucketEncryption"]["ServerSideEncryptionConfiguration"][0]["ServerSideEncryptionByDefault"]["SSEAlgorithm"] == "AES256"
    policies = [resource for resource in template["Resources"].values()
                if resource["Type"] == "AWS::S3::BucketPolicy"
                and resource["Properties"]["Bucket"] == {"Ref": name}]
    assert len(policies) == 1
    denies = graph.resolve(policies[0]["Properties"]["PolicyDocument"])["Statement"]
    tls = [rule for rule in denies if rule.get("Condition") == {"Bool": {"aws:SecureTransport": "false"}}]
    assert len(tls) == 1
    assert tls[0]["Effect"] == "Deny" and tls[0]["Principal"] == "*"
    assert set(tls[0]["Resource"]) == {"arn:aws:s3:::" + graph.values[name],
                                     "arn:aws:s3:::" + graph.values[name] + "/*"}


def test_backup_lifecycle_covers_old_versions_markers_and_abandoned_uploads(template):
    graph = Graph(template)
    bucket = template["Resources"]["BackupBucket"]["Properties"]
    assert bucket["VersioningConfiguration"]["Status"] == "Enabled"
    graph.values["BackupRetentionDays"] = 47
    rules = graph.resolve(bucket["LifecycleConfiguration"])["Rules"]
    enabled = [rule for rule in rules if rule["Status"] == "Enabled"]
    assert any(rule.get("ExpirationInDays") == 47 and rule.get("NoncurrentVersionExpiration", {}).get("NoncurrentDays") == 47
               for rule in enabled)
    assert any(rule.get("ExpiredObjectDeleteMarker") is True for rule in enabled)
    assert any(rule.get("AbortIncompleteMultipartUpload", {}).get("DaysAfterInitiation") == 1 for rule in enabled)
    assert all(rule["Prefix"] == graph.output("BackupPrefix") for rule in enabled)
    assert "LifecycleConfiguration" not in template["Resources"]["ArtifactBucket"]["Properties"]


def test_instance_role_has_only_scoped_artifact_reads_and_backup_writes(template):
    graph = Graph(template)
    role_name, role = graph.only("AWS::IAM::Role")
    profile_name, profile = graph.only("AWS::IAM::InstanceProfile")
    _, instance = graph.only("AWS::EC2::Instance")
    assert profile["Properties"]["Roles"] == [{"Ref": role_name}]
    assert instance["Properties"]["IamInstanceProfile"] == {"Ref": profile_name}
    assert graph.resolve(role["Properties"]["ManagedPolicyArns"]) == ["arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"]
    artifact_objects = "arn:aws:s3:::" + graph.output("ArtifactBucketName") + "/releases/*"
    backup_objects = "arn:aws:s3:::" + graph.output("BackupBucketName") + "/backups/*"
    s3_grants = [rule for rule in statements(graph) if rule["Effect"] == "Allow"
                 and any(action.startswith("s3:") for action in rule["Action"])]
    assert len(s3_grants) == 2
    assert for_action(graph, "s3:GetObject") == [{"Effect": "Allow", "Action": ["s3:GetObject"], "Resource": [artifact_objects]}]
    assert for_action(graph, "s3:PutObject") == [{"Effect": "Allow", "Action": ["s3:PutObject"], "Resource": [backup_objects]}]
    assert all(rule["Action"] in [["s3:GetObject"], ["s3:PutObject"]] for rule in s3_grants)
    assert all("*" not in rule["Resource"] and "arn:aws:s3:::*" not in rule["Resource"] for rule in s3_grants)


def test_metric_publication_permission_is_limited_to_operational_namespace(template):
    graph = Graph(template)
    metrics = for_action(graph, "cloudwatch:PutMetricData")
    assert len(metrics) == 1
    assert metrics[0]["Resource"] == ["*"]  # API has no resource-level permission.
    assert metrics[0]["Condition"] == {"StringEquals": {"cloudwatch:namespace": "ONPF/Operations"}}
    assert not any(action.startswith("logs:") for rule in statements(graph) for action in rule["Action"])


@pytest.mark.parametrize("metric,operator,threshold,statistic,unit", [
    ("ApplicationHealthy", "LessThanThreshold", 1, "Minimum", "Count"),
    ("DataDiskUsedPercent", "GreaterThanOrEqualToThreshold", 85, "Maximum", "Percent"),
    ("BackupAgeHours", "GreaterThanThreshold", 30, "Maximum", "None"),
])
def test_operational_alarms_use_exact_monitor_contract_and_breach_without_reports(template, metric, operator, threshold, statistic, unit):
    graph = Graph(template)
    found = [resource for resource in template["Resources"].values()
             if resource["Type"] == "AWS::CloudWatch::Alarm" and resource["Properties"].get("MetricName") == metric]
    assert len(found) == 1
    props = graph.resolve(found[0]["Properties"])
    assert props["Namespace"] == "ONPF/Operations"
    assert props["Dimensions"] == [{"Name": "StackName", "Value": "agency-onpf"}]
    assert props["Period"] == 300 and props["EvaluationPeriods"] == props["DatapointsToAlarm"] == 3
    assert props["TreatMissingData"] == "breaching"
    assert props["Unit"] == unit
    assert props["OKActions"] == [graph.output("AlarmTopicArn")]
    assert (props["ComparisonOperator"], props["Threshold"], props["Statistic"]) == (operator, threshold, statistic)
    assert props["AlarmActions"] == [graph.output("AlarmTopicArn")]


def test_instance_status_alarm_and_optional_email_are_connected(template):
    graph = Graph(template)
    found = [resource for resource in template["Resources"].values()
             if resource["Type"] == "AWS::CloudWatch::Alarm" and resource["Properties"].get("Namespace") == "AWS/EC2"]
    assert len(found) == 1
    props = graph.resolve(found[0]["Properties"])
    assert props["MetricName"] == "StatusCheckFailed"
    assert props["Dimensions"] == [{"Name": "InstanceId", "Value": "i-0123456789abcdef0"}]
    assert props["AlarmActions"] == [graph.output("AlarmTopicArn")]
    subscription_name, subscription = graph.only("AWS::SNS::Subscription")
    assert subscription_name and subscription["Condition"] in template["Conditions"]
    assert template["Parameters"]["AlarmEmail"]["Default"] == ""
    assert subscription["Properties"]["Endpoint"] == {"Ref": "AlarmEmail"}
    assert subscription["Properties"]["Protocol"] == "email"
    assert graph.resolve(subscription["Properties"]["TopicArn"]) == graph.output("AlarmTopicArn")


def test_alarm_topic_permits_same_account_cloudwatch_without_incompatible_kms_key(template):
    graph = Graph(template)
    _, topic = graph.only("AWS::SNS::Topic")
    assert topic["Properties"].get("KmsMasterKeyId") != "alias/aws/sns"
    _, policy = graph.only("AWS::SNS::TopicPolicy")
    props = graph.resolve(policy["Properties"])
    assert props["Topics"] == [graph.output("AlarmTopicArn")]
    grants = props["PolicyDocument"]["Statement"]
    assert grants == [{"Effect": "Allow", "Principal": {"Service": "cloudwatch.amazonaws.com"},
                       "Action": "sns:Publish", "Resource": graph.output("AlarmTopicArn"),
                       "Condition": {"StringEquals": {"aws:SourceAccount": "123456789012"},
                                     "ArnLike": {"aws:SourceArn": "arn:aws:cloudwatch:ca-central-1:123456789012:alarm:*"}}}]


def test_operator_outputs_resolve_to_actual_resources_and_safe_prefixes(template):
    graph = Graph(template)
    instance_name, _ = graph.only("AWS::EC2::Instance")
    volume_name, _ = graph.only("AWS::EC2::Volume")
    assert graph.output("InstanceId") == graph.values[instance_name]
    assert graph.output("DataVolumeId") == graph.values[volume_name]
    assert graph.output("ArtifactBucketName") == graph.values["ArtifactBucket"]
    assert graph.output("BackupBucketName") == graph.values["BackupBucket"]
    assert graph.output("ArtifactPrefix") == "releases/"
    assert graph.output("BackupPrefix") == "backups/"
    assert graph.output("UbuntuImageId") == "ami-0123456789abcdef0"
    assert graph.output("ApplicationUrl") == "https://onpf.example.org"
    assert graph.output("MetricNamespace") == "ONPF/Operations"
    assert graph.output("MetricStackName") == "agency-onpf"
    assert graph.output("Region") == "ca-central-1"
    assert "ElasticIp" in template["Outputs"]


@pytest.mark.parametrize("region", ["us-east-1", "us-east-2", "us-west-2", "eu-west-1", "ap-southeast-2"])
def test_selected_region_flows_to_outputs_storage_and_alarm_permissions(template, region):
    graph = Graph(template, region=region)
    assert graph.output("Region") == region
    _, subnet = graph.only("AWS::EC2::Subnet")
    _, volume = graph.only("AWS::EC2::Volume")
    zone = graph.resolve(subnet["Properties"]["AvailabilityZone"])
    assert zone.startswith(region)
    assert graph.resolve(volume["Properties"]["AvailabilityZone"]) == zone
    assert graph.output("DataAvailabilityZone") == zone
    _, policy = graph.only("AWS::SNS::TopicPolicy")
    grants = graph.resolve(policy["Properties"]["PolicyDocument"]["Statement"])
    assert grants[0]["Condition"]["ArnLike"]["aws:SourceArn"] == (
        f"arn:aws:cloudwatch:{region}:123456789012:alarm:*")


def test_all_taggable_resources_have_agency_application_and_stack_identity(template):
    # Association/policy resources and InstanceProfile do not support Tags.
    taggable = {"AWS::EC2::VPC", "AWS::EC2::Subnet", "AWS::EC2::InternetGateway",
                "AWS::EC2::RouteTable", "AWS::EC2::SecurityGroup", "AWS::EC2::Instance",
                "AWS::EC2::Volume", "AWS::EC2::EIP", "AWS::S3::Bucket", "AWS::IAM::Role",
                "AWS::SNS::Topic", "AWS::CloudWatch::Alarm"}
    graph = Graph(template)
    for name, resource in template["Resources"].items():
        if resource["Type"] in taggable:
            tags = {tag["Key"]: graph.resolve(tag["Value"]) for tag in resource["Properties"]["Tags"]}
            assert tags["Application"] == "ONPF", name
            assert tags["Stack"] == "agency-onpf", name
            assert tags["Agency"], name


def test_parameter_example_matches_types_patterns_and_allowed_values(template):
    assert EXAMPLES.exists(), "CloudFormation parameter examples have not been implemented"
    examples = json.loads(EXAMPLES.read_text(encoding="utf-8"))
    values = {example["ParameterKey"]: example["ParameterValue"] for example in examples}
    assert len(values) == len(examples)
    assert set(values) == set(template["Parameters"])
    for name, parameter in template["Parameters"].items():
        value = values[name]
        assert isinstance(value, str)
        if "AllowedPattern" in parameter:
            assert re.fullmatch(parameter["AllowedPattern"], value), name
        if "AllowedValues" in parameter:
            assert value in parameter["AllowedValues"], name
        if parameter["Type"] == "Number":
            assert parameter["MinValue"] <= int(value) <= parameter["MaxValue"], name


def test_bootstrap_authenticates_native_cli_before_install_and_jobs_use_it(template):
    from test_aws_tools import tool
    graph = Graph(template)
    _, instance = graph.only('AWS::EC2::Instance')
    bootstrap = base64.b64decode(graph.resolve(instance['Properties']['UserData'])).decode()
    assert 'snap install aws-cli' not in bootstrap
    assert 'awscli-exe-linux-x86_64-2.37.4.zip' in bootstrap
    assert 'FB5DB77FD5C118B80511ADA8A6310ACC4672475C' in bootstrap
    assert '-----BEGIN PGP PUBLIC KEY BLOCK-----' in bootstrap
    assert bootstrap.index('gpg --batch --verify') < bootstrap.index('unzip -q') < bootstrap.index('./aws/install')
    assert 'set -euo pipefail' in bootstrap
    assert '--bin-dir /usr/local/bin --install-dir /usr/local/aws-cli' in bootstrap
    assert tool('backup').AWS == ['/usr/local/bin/aws']
    assert '/usr/local/bin/aws --version' in bootstrap
    assert len(bootstrap.encode()) < 16384  # EC2 raw user-data size limit
    for name in ('onpf-backup.service', 'onpf-monitor.service'):
        unit = (ROOT / 'deploy/aws/templates' / name).read_text()
        assert 'NoNewPrivileges=true' in unit
        assert 'CapabilityBoundingSet=\n' in unit
        assert 'ProtectSystem=strict' in unit
        assert 'User=onpf' in unit
        assert '/deploy/aws/backup.py' in unit


@pytest.mark.parametrize('failure', ['import', 'fingerprint', 'signature'])
def test_native_cli_bootstrap_stops_before_extraction_on_authentication_failure(template, tmp_path, failure):
    import os
    import shutil
    import subprocess
    graph = Graph(template)
    _, instance = graph.only('AWS::EC2::Instance')
    bootstrap = base64.b64decode(graph.resolve(instance['Properties']['UserData'])).decode()
    assert '# BEGIN authenticated native AWS CLI' in bootstrap
    fragment = bootstrap.split('# BEGIN authenticated native AWS CLI', 1)[1].split('# END authenticated native AWS CLI', 1)[0]
    if os.name == 'nt':
        shell = Path(shutil.which('git')).resolve().parents[1] / 'usr/bin/sh.exe'
        if not shell.is_file(): pytest.skip('Git Bash unavailable for shell failure injection')
    else:
        shell = shutil.which('bash')
        if not shell: pytest.skip('Bash unavailable for shell failure injection')
    (tmp_path / 'stage').mkdir()
    # External download/GPG boundaries are injected; run the real shell control
    # flow. Never invoke apt, real downloads, root writes, removal, or installer.
    prelude = r'''
set -euo pipefail
export PATH=/usr/bin:$PATH
mktemp() { printf './stage\n'; }
rm() { :; }
mkdir() { :; }
curl() { :; }
gpg() {
    printf '%s\n' "$*" >> ../gpg-calls
    case "$*" in
        *--import*) test "$FAILURE" != import ;;
        *--fingerprint*)
            if test "$FAILURE" = fingerprint; then printf 'fpr:::::::::WRONG:\n';
            else printf 'fpr:::::::::FB5DB77FD5C118B80511ADA8A6310ACC4672475C:\n'; fi ;;
        *--verify*) test "$FAILURE" != signature ;;
        *) return 99 ;;
    esac
}
unzip() { printf 'UNSAFE extraction\n' > ../extracted; return 99; }
'''
    result = subprocess.run([str(shell), '-c', prelude + fragment], cwd=tmp_path,
                            env={**os.environ, 'FAILURE': failure}, capture_output=True, text=True)
    assert result.returncode != 0
    assert not (tmp_path / 'extracted').exists()
    assert (tmp_path / 'gpg-calls').exists(), result.stderr
    calls = (tmp_path / 'gpg-calls').read_text()
    assert '--import' in calls
    assert ('--verify' in calls) == (failure == 'signature')

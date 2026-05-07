cd /home/user/oss_checks
python cpluschecks/scanner.py /home/user/jobs/<product>/sources/root_sources \
    --sbom /tmp/test_cplus.json \
    --unknown /tmp/test_cplus_unknown.json

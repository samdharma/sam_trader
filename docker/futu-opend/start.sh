#!/bin/bash

FUTU_OPEND_RSA_FILE_PATH=/.futu/futu.pem
FUTU_OPEND_IP=${FUTU_OPEND_IP:-$(cat /etc/hostname)}

if [ -z "$FUTU_ACCOUNT_PWD_MD5" ]; then
  if [ -n "$FUTU_ACCOUNT_PWD" ]; then
    echo "WARNING: FUTU_ACCOUNT_PWD is deprecated; set FUTU_ACCOUNT_PWD_MD5 instead." >&2
  fi
  FUTU_ACCOUNT_PWD_MD5=$(echo -n "$FUTU_ACCOUNT_PWD" | md5sum | awk '{print $1}')
fi

echo "FUTU_ACCOUNT_ID: $FUTU_ACCOUNT_ID"
echo "FUTU_OPEND_RSA_FILE_PATH: $FUTU_OPEND_RSA_FILE_PATH"
echo "FUTU_OPEND_IP: $FUTU_OPEND_IP"

FUTU_OPEND_XML_SRC=/bin/FutuOpenD.xml
FUTU_OPEND_XML_PATH=/tmp/FutuOpenD.xml

echo "Copy and configure FutuOpenD.xml"

cp "$FUTU_OPEND_XML_SRC" "$FUTU_OPEND_XML_PATH"

sed -i "s|.*</ip>|$FUTU_OPEND_IP</ip>|" $FUTU_OPEND_XML_PATH
sed -i "s|.*</api_port>|$FUTU_OPEND_PORT</api_port>|" $FUTU_OPEND_XML_PATH
sed -i "s|.*</telnet_ip>|$FUTU_OPEND_IP</telnet_ip>|" $FUTU_OPEND_XML_PATH
sed -i "s|###FUTU_ACCOUNT_ID###|$FUTU_ACCOUNT_ID|" $FUTU_OPEND_XML_PATH
sed -i "s|###FUTU_ACCOUNT_PWD_MD5###|$FUTU_ACCOUNT_PWD_MD5|" $FUTU_OPEND_XML_PATH
sed -i "s|###FUTU_OPEND_RSA_FILE_PATH###|$FUTU_OPEND_RSA_FILE_PATH|" $FUTU_OPEND_XML_PATH

if [ -n "$FUTU_OPEND_TELNET_PORT" ]; then
  sed -i "s|###FUTU_OPEND_TELNET_PORT###|$FUTU_OPEND_TELNET_PORT|" $FUTU_OPEND_XML_PATH
else
  sed -i "s|.*</telnet_port>||" $FUTU_OPEND_XML_PATH
fi

/bin/FutuOpenD -cfg_file=$FUTU_OPEND_XML_PATH

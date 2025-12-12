
nano /etc/postfix/virtual_alias

@ozenses.com    mail-tester

systemctl restart postfix dovecot
sudo postmap /etc/postfix/virtual_alias
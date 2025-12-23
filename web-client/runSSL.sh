THIS_IP=`ip route get 1.1.1.1 | awk '{print $7}'`
DEV_SSL_CERT=../dev-assets/certs/local-cert.pem DEV_SSL_KEY=../dev-assets/certs/local-key.pem VITE_API_BASE_URL=https://$THIS_IP:8443 npm run dev -- --host


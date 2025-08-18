web: curl -L -o xray.zip https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-64.zip \
    && unzip -o xray.zip \
    && chmod +x xray \
    && echo "✅ Xray downloaded and ready" \
    && ./xray -config xray.json & \
    sleep 5 && curl -s https://api.ipify.org && echo " 🌍 <- Proxy public IP" \
    && python main.py

# TSShara Android App

App Android complementar para o monitor de nobreak TSShara CLI.
Monitore seu nobreak TS Shara de qualquer lugar na sua rede local/internet.

## Funcionalidades

- **Status em tempo real** — tensão de entrada/saída, bateria, temperatura, flags
- **Saúde do serviço** — uptime, falhas, porta serial
- **Info do dispositivo** — hardware ID, hostname, plataforma
- **Notificações push** — alertas de falha de energia, bateria baixa, falha do nobreak
- **Sobrevive a reinicialização** — monitoramento continua após desligar/ligar o dispositivo
- **Notificação de teste** — botão para testar que as notificações estão funcionando
- **Bilíngue** — Português (BR) e English, configurável nas opções
- **Configurável** — endereço do servidor, porta, HTTPS, autenticação Basic Auth
- **Responsivo** — Material Design 3 com suporte a temas dinâmicos

## Requisitos

- Android 15 (API 35)
- Servidor tsshara-cli rodando com API habilitada (`[api] enabled = true` no config.ini)

## Setup

1. No servidor, habilite a API no `config.ini`:
   ```ini
   [api]
   enabled = true
   host = 0.0.0.0
   port = 8080
   ```

2. Inicie o monitor: `python tsshara-cli.py monitor`

3. No app, vá em **Configurações** e insira:
   - Endereço do servidor (IP na rede local)
   - Porta (padrão: 8080)
   - Credenciais de autenticação (se configurado)

4. Toque em **Testar Conexão** para verificar

5. Habilite **Monitoramento em Segundo Plano** para receber notificações

## Build

```bash
cd android-app
./gradlew assembleDebug
```

O APK será gerado em `app/build/outputs/apk/debug/app-debug.apk`

## Arquitetura

- **Linguagem:** Kotlin
- **UI:** Jetpack Compose + Material 3
- **Rede:** HttpURLConnection (zero dependências extras)
- **Configurações:** Jetpack DataStore
- **Background:** WorkManager (persiste após reboot)
- **Notificações:** NotificationManager + NotificationChannel
- **Min SDK:** 35 (Android 15)

## Estrutura

```
app/src/main/java/com/tsshara/app/
├── TssharaApp.kt           # Application — cria canal de notificação
├── MainActivity.kt          # Activity principal — navegação, permissões, idioma
├── data/
│   └── PrefsManager.kt      # DataStore — configurações persistidas
├── network/
│   └── ApiClient.kt         # Cliente HTTP — chamadas à API REST
├── service/
│   ├── NotificationHelper.kt # Notificações — canal + envio
│   ├── UpsMonitorWorker.kt   # WorkManager — polling periódico em background
│   └── BootReceiver.kt       # BroadcastReceiver — re-agenda após reboot
└── ui/
    ├── StatusScreen.kt       # Tela de status do nobreak
    ├── SettingsScreen.kt     # Tela de configurações
    └── theme/
        ├── Color.kt          # Paleta de cores
        └── Theme.kt          # Tema Material 3
```

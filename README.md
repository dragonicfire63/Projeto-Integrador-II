# Conexo

Aplicação de anotações feita com Django e SQLite. As páginas HTML existentes são servidas pelos templates do Django; contas, notas, post-its, eventos e preferências ficam no banco de dados.

## Executar localmente

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

Abra http://127.0.0.1:8000/ e crie uma conta pela tela inicial. Para administrar o banco pelo painel Django, crie um usuário administrativo com `python manage.py createsuperuser` e acesse `/admin/`.

## Backup no Google Drive

Para habilitar a conexão Google Drive, crie um cliente OAuth do tipo aplicação Web no Google Cloud Console, ative a Google Drive API e cadastre como URI de redirecionamento autorizado `http://127.0.0.1:8000/api/backup/google/callback/`. Configure as variáveis de ambiente antes de iniciar o servidor:

```bash
export GOOGLE_OAUTH_CLIENT_ID="seu-client-id"
export GOOGLE_OAUTH_CLIENT_SECRET="seu-client-secret"
export GOOGLE_OAUTH_REDIRECT_URI="http://127.0.0.1:8000/api/backup/google/callback/"
```

Os tokens OAuth são criptografados com `DJANGO_SECRET_KEY`. Em produção, use uma chave persistente e mantenha segredos fora do repositório. O backup do Drive é salvo na área privada `appDataFolder` do aplicativo. A quota livre mostrada é a quota geral retornada pelo Google Drive.

Para executar os backups automáticos na frequência definida pelo usuário, agende o comando Django a cada hora no cron ou no agendador do servidor:

```cron
0 * * * * /caminho/do/projeto/.venv/bin/python /caminho/do/projeto/manage.py run_due_backups
```

O comando ignora contas sem Drive conectado e preferências com backup automático desligado. Exportar baixa um JSON local; restaurar importa um JSON Conexo e substitui as notas e pastas atuais da conta. A restauração do Google Drive também substitui esses dados e pede confirmação na interface.

O SQLite e o servidor `runserver` são adequados para desenvolvimento local. Antes de publicar, configure `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS` e `DJANGO_DEBUG=0`, além de usar um servidor WSGI/ASGI de produção e um banco apropriado.# Projeto-Integrador-II
# Projeto-Integrador-II
# Projeto-Integrador-II
# Projeto-Integrador-II

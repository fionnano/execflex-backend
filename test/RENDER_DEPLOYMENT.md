# Running Smoke Tests in Render Pipeline

## Option 1: Pre-Deploy Command (Recommended for Quick Validation)

Add the smoke test as a **Pre-Deploy Command** in your Render service settings:

1. Go to your Render Dashboard → Your Service → Settings
2. Scroll to **Pre-Deploy Command**
3. Add:
   ```bash
   cd backend/test && chmod +x smoke_test.sh && ./smoke_test.sh https://execflex-backend-1.onrender.com
   ```

**Note:** This runs tests against the currently running service before deploying the new version. If tests fail, the deployment is blocked.

## Option 2: GitHub Actions (Recommended for Full CI/CD)

The smoke tests are automatically configured to run via GitHub Actions:

- **On push to main/master**: Tests run automatically against Render production
- **Manual trigger**: Use "Run workflow" in GitHub Actions with optional custom API URL
- **On Render webhook**: Configure Render to send deployment webhooks to trigger tests

### Setup Render Webhook (Optional)

1. In Render Dashboard → Your Service → Settings → Webhooks
2. Add webhook URL: `https://api.github.com/repos/YOUR_USERNAME/YOUR_REPO/dispatches`
3. Add header: `Authorization: token YOUR_GITHUB_TOKEN`
4. Add payload: `{"event_type": "render-deploy"}`

## Option 3: Manual Testing

Run smoke tests manually after deployment:

```bash
cd backend/test
./smoke_test.sh https://execflex-backend-1.onrender.com
```

## Auto-Detection

The smoke test script automatically detects the Render environment:
- Uses `RENDER_SERVICE_URL` if available (when running in Render)
- Falls back to `https://execflex-backend-1.onrender.com` if no URL provided
- Can override with command-line argument


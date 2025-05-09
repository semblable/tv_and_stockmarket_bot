# Azure Flask Dashboard Deployment Plan (Part 3b)

This document outlines the steps to deploy the Flask dashboard to Azure App Service for Containers.

## Current State (Assumed from Part 3a)

*   Azure Resource Group created (e.g., `tvshow-bot-rg`).
*   Azure Container Registry (ACR) created (e.g., `youruniquecrname.azurecr.io`), and dashboard Docker image (`flask-dashboard:latest`) is pushed.
*   Azure Key Vault created (e.g., `youruniquevaultname`), with all necessary secrets stored.
*   The Discord bot is running on ACI (e.g., `tvshow-discord-bot`), and its FQDN (e.g., `tvshow-bot-api-yourinitials.yourregion.azurecontainer.io`) is known. This FQDN points to the bot's internal API on port 5000.

## Mermaid Diagram

```mermaid
graph TD
    subgraph Azure Setup
        A[User's Machine] -- Docker Image --> B(Azure Container Registry <br/> youruniquecrname.azurecr.io/flask-dashboard:latest)
        C[User's Machine] -- Azure CLI Commands --> D{Azure Resource Group <br/> tvshow-bot-rg}
    end

    subgraph Dashboard Deployment (Part 3b)
        D -- Creates --> E(App Service Plan <br/> tvshow-dashboard-plan <br/> SKU: F1, Linux)
        D -- Creates --> F(Web App for Containers <br/> tvshow-dashboard-yourinitials)
        B -- Deploys Image To --> F
        F -- System-Assigned Managed Identity --> G(Azure Key Vault <br/> youruniquevaultname)
        G -- Grants Access To --> F
        H(ACI - Discord Bot <br/> tvshow-bot-api-yourinitials.yourregion.azurecontainer.io) -- API URL --> F
        F -- App Settings (Env Vars) <br/> - WEBSITES_PORT=8000 <br/> - Key Vault Refs <br/> - INTERNAL_API_BASE_URL --> I(Running Dashboard <br/> tvshow-dashboard-yourinitials.azurewebsites.net)
    end

    subgraph External Services
        J[Discord Developer Portal] -- Update Redirect URI --> I
        K[End User] -- Accesses --> I
    end

    style B fill:#f9f,stroke:#333,stroke-width:2px
    style G fill:#f9f,stroke:#333,stroke-width:2px
    style H fill:#ccf,stroke:#333,stroke-width:2px
```

## Deployment Steps:

1.  **Create an App Service Plan:**
    *   **Explanation:** An App Service Plan defines the underlying compute resources (location, size, features) for your web app. We'll use the Free (F1) tier on Linux, suitable for development and testing.
    *   **Azure CLI Command:**
        ```bash
        az appservice plan create \
          --resource-group <your_resource_group_name> \
          --name tvshow-dashboard-plan \
          --sku F1 \
          --is-linux
        ```
    *   **Action:** Replace `<your_resource_group_name>` with your actual resource group name (e.g., `tvshow-bot-rg`).

2.  **Create Web App for Containers:**
    *   **Explanation:** This step provisions the Azure App Service instance that will pull and run your `flask-dashboard:latest` Docker image from Azure Container Registry (ACR). A system-assigned Managed Identity will also be created for secure access to other Azure resources like Key Vault.
    *   **Azure CLI Command:**
        ```bash
        az webapp create \
          --resource-group <your_resource_group_name> \
          --plan tvshow-dashboard-plan \
          --name tvshow-dashboard-<your_initials> \
          --deployment-container-image-name <your_acr_name>.azurecr.io/flask-dashboard:latest \
          --docker-registry-server-url https://<your_acr_name>.azurecr.io \
          --docker-registry-server-user <your_acr_username> \
          --docker-registry-server-password <your_acr_password> \
          --assign-identity [system]
        ```
    *   **Actions:**
        *   Replace `<your_resource_group_name>` (e.g., `tvshow-bot-rg`).
        *   Replace `tvshow-dashboard-<your_initials>` with a globally unique name for your web app. This will be part of its URL.
        *   Replace `<your_acr_name>` with your ACR name (e.g., `youruniquecrname`).
        *   Replace `<your_acr_username>` with your ACR username.
        *   Replace `<your_acr_password>` with your ACR password (the one used to push the image).
    *   **Note:** After this command, the Web App will have a Managed Identity. We'll need its `principalId` (Object ID) for the next step.

3.  **Grant Web App's Managed Identity Access to Key Vault:**
    *   **Explanation:** To allow the Web App to securely read secrets from Azure Key Vault (like API keys and client secrets) without embedding them in code or configuration, we grant its Managed Identity specific permissions.
    *   **Get Managed Identity Principal ID:**
        ```bash
        az webapp identity show \
          --resource-group <your_resource_group_name> \
          --name tvshow-dashboard-<your_initials> \
          --query principalId \
          --output tsv
        ```
        *(Store this `principalId` for the next command.)*
    *   **Azure CLI Command to Set Key Vault Policy:**
        ```bash
        az keyvault set-policy \
          --resource-group <your_resource_group_name> \
          --name <your_key_vault_name> \
          --object-id <webapp_principal_id_from_above> \
          --secret-permissions get list
        ```
    *   **Actions:**
        *   Replace `<your_resource_group_name>` (e.g., `tvshow-bot-rg`).
        *   Replace `tvshow-dashboard-<your_initials>` with your Web App name.
        *   Replace `<your_key_vault_name>` with your Key Vault name (e.g., `youruniquevaultname`).
        *   Replace `<webapp_principal_id_from_above>` with the `principalId` obtained from the `az webapp identity show` command.

4.  **Configure Web App Application Settings (Environment Variables):**
    *   **Explanation:** Application Settings in App Service are used to inject environment variables into your container. We will use these to provide configuration to the Flask app, including references to secrets stored in Key Vault and the URL of your bot's API running on ACI.
    *   **Azure CLI Command:**
        ```bash
        az webapp config appsettings set \
          --resource-group <your_resource_group_name> \
          --name tvshow-dashboard-<your_initials> \
          --settings \
            WEBSITES_PORT=8000 \
            DASHBOARD_CLIENT_ID="@Microsoft.KeyVault(SecretUri=https://<your_key_vault_name>.vault.azure.net/secrets/DASHBOARD-CLIENT-ID)" \
            DASHBOARD_CLIENT_SECRET="@Microsoft.KeyVault(SecretUri=https://<your_key_vault_name>.vault.azure.net/secrets/DASHBOARD-CLIENT-SECRET)" \
            DASHBOARD_SECRET_KEY="@Microsoft.KeyVault(SecretUri=https://<your_key_vault_name>.vault.azure.net/secrets/DASHBOARD-SECRET-KEY)" \
            INTERNAL_API_KEY="@Microsoft.KeyVault(SecretUri=https://<your_key_vault_name>.vault.azure.net/secrets/INTERNAL-API-KEY)" \
            INTERNAL_API_BASE_URL="http://<bot_aci_fqdn>:5000" \
            TMDB_API_KEY="@Microsoft.KeyVault(SecretUri=https://<your_key_vault_name>.vault.azure.net/secrets/TMDB-API-KEY)"
        ```
    *   **Actions:**
        *   Replace `<your_resource_group_name>` (e.g., `tvshow-bot-rg`).
        *   Replace `tvshow-dashboard-<your_initials>` with your Web App name.
        *   Replace `<your_key_vault_name>` (e.g., `youruniquevaultname`) in all `SecretUri` paths.
        *   Replace `<bot_aci_fqdn>` with the Fully Qualified Domain Name of your ACI instance running the bot (e.g., `tvshow-bot-api-yourinitials.yourregion.azurecontainer.io`).

5.  **Update Discord Application OAuth2 Redirect URI:**
    *   **Explanation:** The Discord application needs to know the valid URL to redirect users back to after they authenticate. This must now be updated to your new App Service dashboard's callback URL.
    *   **Action:**
        1.  Go to the [Discord Developer Portal](https://discord.com/developers/applications).
        2.  Select your application that corresponds to the dashboard.
        3.  Navigate to the "OAuth2" section (usually under "General Information" or its own tab).
        4.  Find the "Redirect URIs" (or similar) field.
        5.  Add or update the URI to: `https://tvshow-dashboard-<your_initials>.azurewebsites.net/callback`
            *   Ensure you replace `tvshow-dashboard-<your_initials>` with your actual Web App name.
            *   Make sure it's `https` and the path is `/callback`.
        6.  Save the changes in the Discord Developer Portal.

6.  **Verify Dashboard Deployment:**
    *   **Explanation:** After completing the above steps and allowing a few minutes for the App Service to pull the container and start, you should be able to access your dashboard.
    *   **Actions:**
        1.  Open your web browser and navigate to: `https://tvshow-dashboard-<your_initials>.azurewebsites.net`
            *   Replace `tvshow-dashboard-<your_initials>` with your Web App name.
        2.  Test the login functionality and other dashboard features.
        3.  **Troubleshooting:** If you encounter issues, check the logs:
            *   **Azure Portal:** Navigate to your Web App -> "Monitoring" -> "Log stream".
            *   **Azure CLI:**
                ```bash
                az webapp log tail \
                  --resource-group <your_resource_group_name> \
                  --name tvshow-dashboard-<your_initials>
                ```
                (Replace placeholders accordingly).
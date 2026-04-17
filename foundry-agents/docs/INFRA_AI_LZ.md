# AI Landing Zone Reference Implementation

This reference implementation should be used for all production deployments including any test environment where customer data is being processed by our agents. For development and POC deployments, please refer to the folder [Standard Infrastructure for the AI Agents](./INFRA_STANDARD.md).

For details about the Reference Architecture for this implementation and how to deploy it, refer to https://aka.ms/ailz:
- [Presentation](https://aka.ms/ailz/presentation)
- [Documentation](https://aka.ms/ailz/documentation)
- [Diagram](https://aka.ms/ailz/diagram)
- [Repository](https://aka.ms/ailz)
- [Video](https://aka.ms/ailz/video)
- [Terraform pattern module](https://aka.ms/ailz/terraform)
- [Bicep pattern module](https://aka.ms/ailz/bicep)


After the automated deployment of this Reference Architecture, there are few modifications to make to the environment for our solution to work. These changes are detailed below.

After those changes are made, you can deploy the Insights-Agent by following [these instructions](../README.md).

## Manual Changes required

After the deployment, follow the steps below to make the changes required to the platform for our solution to work.

1. Assign yourself the following roles so that you have data plane access to the different Azure services used:

    - "Search Index Data Reader" in the AI Search service mentioned above, so that you can use the index explorer to query the indexed data.
    - "Blob Storage Contributor" in the Storage Account mentioned above, so that you can create containers and upload some sample application documents.
    - "Table Storage Reader" in the same Storage Account so that you can view the tables created by the agent.

2. Connect to the Jumpbox VM as you will only be able to access the data plane of the Azure services from the private network. Retrieve the passwords used to connect to this VM and also the Build VM from the Key Vault by following these steps:

    - Assign this keyVault your IP address so that you can access it over the Internet to retrieve the passwords for both VMs. Note that this is for testing and in a production environment, you would not be using this approach.
    - Assign yourself temporarely the role of 'Key Vault Secrets officier' to this Key Vault instance so that you can retrieve the passwords.
    - Connect trough Bastion Host to these VMs using the username 'azureuser' and the passwords retrieved from Key Vault. The secret that has the word "jump" in its name contains the password for the Jumpbox, while the other secret contains the password for the Build agent that we will be using as our Actions Workflow runner.
      -After you sign in as admin, create a local account for yourself on the VM.

3. From the Azure Storage Account not used by the Azure Foundry, add a Private Endpoint for the target sub-resource "Table" in the PrivateEndpointSubnet.

4. Assign the System Assigned Managed Identity of the AI Search service used by the Foundry the roles:

    - "Storage Blob Data Reader" on the previous Azure Storage Account.
    - "Cognitive Services OpenAI Contributor" on the Azure Foundry service.
    - "Cognitive Services Contributor" on the Azure Foundry service.

5. Assign the System Assigned Managed Identity of the Foundry **Project** the roles:

   - "Search Index Data Reader" on the Azure AI Search service mentioned above.
   - "Search Service Contributor" on the Azure AI Search service mentioned above.

6. In the same Azure AI Search instance:
    - Select the **Standard** plan from Settings ==> Premium features.
    - Set the API Access Control to "Role-based-access-control" from Settings ==> Keys

7. Create the following two [Shared Private Links (SPLs)](https://learn.microsoft.com/en-us/azure/search/search-indexer-howto-access-private?tabs=portal-create) in the above AI Search service, so that it can access these services over private endpoints:

    - **openai_account** in Azure Foundry. This is required to allow [Integrated vectorization](https://learn.microsoft.com/en-us/azure/search/vector-search-integrated-vectorization) to work.
    - **blob** in Azure Storage Account [ai1stimggenaisarbfk](https://bst-7f095838-ec49-4977-84be-7798b30bfd3d.bastion.azure.com/#/client/YWkxc3RtZzEtanVtcABjAGJpZnJvc3Q=?trustedAuthority=https:%2F%2Fhybridnetworking.hosting.portal.azure.net)

8. Create an App Insights instance if it doesn't exit.

9. Add a connection to the App Insights that you created previously from the Foundry portal. Make sure you add it at the Project level and not the Account level.

10. From the Foundry portal, deploy the Embedding Model **text-embedding-3-large** so that Azure AI Search can index the files it reads from the Azure Blob Storage.

11. Deploy the GitHub Actions runners per these instructions [README](../../.github/workflows/README.md)

**Note that these steps are being automated as part if the landing zone documented in this repo: https://github.com/mcaps-microsoft/ai-first-migrate-ailz**



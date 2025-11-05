# SCP Wrap

SCP wrap was written to upload data from a shared instrument PC to your own market or vault share. It uses OIDC authentication and SSH certificates to differentiate between users and give access to your share without compromising your data to other users of the instrument PC. In this way you will no longer need to directly mount your market or vault to a shared PC where you can forget to disconnect it compromising your data.

![SCP Wrap Diagram](./scpwrap.png)

## User Dependencies
- A google account mapped to a user on the sshauthz server
- A [market](https://docs.erc.monash.edu/RDS/UserGuides/MarketStorageUserGuide/) and/or [vault](https://docs.erc.monash.edu/RDS/UserGuides/VaultStorageUserGuide/) share to move data to - if you don't have one you will need to apply for one
- A [market](https://docs.erc.monash.edu/RDS/UserGuides/MarketStorageUserGuide/) and/or [vault](https://docs.erc.monash.edu/RDS/UserGuides/VaultStorageUserGuide/) share mapped on the Data Transfer Gateway

## Installation

### PyCrucible Installation (Production)
Use this if your computer is locked down and has running scripts disabled.
1. Download scpwrap.exe from [GitHub](https://github.com/HecticHPCSolutions/ScpWrap/releases/)
2. Create an installation folder and copy `scpwrap.exe` to the installation folder
3. Launching the PyCrucible executable will unpack the PyCrucible payload into the installation folder and launch the application

### Virtual Environment Installation (Development)
Use this if you have more control over your computer or if you're a developer
1. In the script's working directory: `python3 -m venv .venv`
2. Activate the environment with:  
   1. Unix: `source .venv/bin/activate`  
   2. Windows: `.venv/Scripts/activate`
3. `pip install -r src/requirements.txt`
4. `pip install pycrucible`

### PyCrucible Build
Use this to build the scpwrap.exe file from source
1. Activate the environment with:  
   1. Unix: `source .venv/bin/activate`  
   2. Windows: `.venv/Scripts/activate`
2. `pycrucible -e src -o scpwrap.exe`

## Instructions
0. Activate the virtual environment if you are using one
1. Double click on `scpwrap.exe` or run the script in the command line
2. Select a folder to move  
   ![Folder Selection](./folder.png)
3. Confirm if you are deleting the dataset after moving  
   ![Confirm](./confirm.png)
4. Authenticate with your Google account (your Monash details) in the popup web browser  
   ![Login](./login.png) ![Google](./google.png) ![Okta](./okta.png)
5. Save your certificate  
   ![Save Certificate](./save_cert.png)
6.  Close the web browser
7.  Leave the process running as it moves your data
8.  Press ENTER to exit
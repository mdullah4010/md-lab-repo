@echo off
setlocal enabledelayedexpansion

:: Script to extract Kubernetes resources per namespace and cluster-wide resources
:: Usage: aksContainerAssessment.bat [namespace1 namespace2 namespace3 ...]
:: If no namespaces provided, script will exit with error

:: Check if namespaces are provided
if "%~1"=="" (
    echo Error: No namespaces provided.
    echo Usage: %~nx0 [namespace1 namespace2 namespace3 ...]
    echo Example: %~nx0 default kube-system production
    exit /b 1
)

:: Create output directory if it doesn't exist
if not exist output mkdir output

echo ========================================
echo Kubernetes Resource Extraction Tool
echo ========================================
echo.

echo ========================================
echo Kubernetes Resource Extraction Tool
echo ========================================
echo.

:: Process each namespace provided as parameter
:process_namespaces
if "%~1"=="" goto cluster_resources

set "NAMESPACE=%~1"
echo ========================================
echo Processing Namespace: !NAMESPACE!
echo ========================================

:: Check if namespace exists
kubectl get namespace !NAMESPACE! >nul 2>&1
if errorlevel 1 (
    echo WARNING: Namespace '!NAMESPACE!' does not exist. Skipping...
    echo.
    shift
    goto process_namespaces
)

:: Create namespace directory
if not exist "output\!NAMESPACE!" mkdir "output\!NAMESPACE!"

:: Extract Deployments
echo [!NAMESPACE!] Extracting Deployments...
for /f "tokens=1" %%D in ('kubectl get deployments -n !NAMESPACE! -o custom-columns^=NAME:.metadata.name --no-headers 2^>nul') do (
    if not "%%D"=="" (
        kubectl get deployment %%D -n !NAMESPACE! -o yaml > "output\!NAMESPACE!\deployment-%%D.yaml" 2>nul
        if not errorlevel 1 (
            echo   - deployment-%%D.yaml
        )
    )
)

:: Extract Services
echo [!NAMESPACE!] Extracting Services...
for /f "tokens=1" %%S in ('kubectl get services -n !NAMESPACE! -o custom-columns^=NAME:.metadata.name --no-headers 2^>nul') do (
    if not "%%S"=="" (
        kubectl get service %%S -n !NAMESPACE! -o yaml > "output\!NAMESPACE!\service-%%S.yaml" 2>nul
        if not errorlevel 1 (
            echo   - service-%%S.yaml
        )
    )
)

:: Extract ConfigMaps
echo [!NAMESPACE!] Extracting ConfigMaps...
for /f "tokens=1" %%C in ('kubectl get configmaps -n !NAMESPACE! -o custom-columns^=NAME:.metadata.name --no-headers 2^>nul') do (
    if not "%%C"=="" (
        kubectl get configmap %%C -n !NAMESPACE! -o yaml > "output\!NAMESPACE!\configmap-%%C.yaml" 2>nul
        if not errorlevel 1 (
            echo   - configmap-%%C.yaml
        )
    )
)

:: Extract Secrets
echo [!NAMESPACE!] Extracting Secrets...
for /f "tokens=1" %%E in ('kubectl get secrets -n !NAMESPACE! -o custom-columns^=NAME:.metadata.name --no-headers 2^>nul') do (
    if not "%%E"=="" (
        kubectl get secret %%E -n !NAMESPACE! -o yaml > "output\!NAMESPACE!\secret-%%E.yaml" 2>nul
        if not errorlevel 1 (
            echo   - secret-%%E.yaml
        )
    )
)

:: Extract Ingress
echo [!NAMESPACE!] Extracting Ingress...
for /f "tokens=1" %%I in ('kubectl get ingress -n !NAMESPACE! -o custom-columns^=NAME:.metadata.name --no-headers 2^>nul') do (
    if not "%%I"=="" (
        kubectl get ingress %%I -n !NAMESPACE! -o yaml > "output\!NAMESPACE!\ingress-%%I.yaml" 2>nul
        if not errorlevel 1 (
            echo   - ingress-%%I.yaml
        )
    )
)

:: Extract PersistentVolumeClaims
echo [!NAMESPACE!] Extracting PersistentVolumeClaims...
for /f "tokens=1" %%P in ('kubectl get pvc -n !NAMESPACE! -o custom-columns^=NAME:.metadata.name --no-headers 2^>nul') do (
    if not "%%P"=="" (
        kubectl get pvc %%P -n !NAMESPACE! -o yaml > "output\!NAMESPACE!\pvc-%%P.yaml" 2>nul
        if not errorlevel 1 (
            echo   - pvc-%%P.yaml
        )
    )
)

:: Extract StatefulSets
echo [!NAMESPACE!] Extracting StatefulSets...
for /f "tokens=1" %%T in ('kubectl get statefulsets -n !NAMESPACE! -o custom-columns^=NAME:.metadata.name --no-headers 2^>nul') do (
    if not "%%T"=="" (
        kubectl get statefulset %%T -n !NAMESPACE! -o yaml > "output\!NAMESPACE!\statefulset-%%T.yaml" 2>nul
        if not errorlevel 1 (
            echo   - statefulset-%%T.yaml
        )
    )
)

:: Extract DaemonSets
echo [!NAMESPACE!] Extracting DaemonSets...
for /f "tokens=1" %%A in ('kubectl get daemonsets -n !NAMESPACE! -o custom-columns^=NAME:.metadata.name --no-headers 2^>nul') do (
    if not "%%A"=="" (
        kubectl get daemonset %%A -n !NAMESPACE! -o yaml > "output\!NAMESPACE!\daemonset-%%A.yaml" 2>nul
        if not errorlevel 1 (
            echo   - daemonset-%%A.yaml
        )
    )
)

:: Extract Jobs
echo [!NAMESPACE!] Extracting Jobs...
for /f "tokens=1" %%J in ('kubectl get jobs -n !NAMESPACE! -o custom-columns^=NAME:.metadata.name --no-headers 2^>nul') do (
    if not "%%J"=="" (
        kubectl get job %%J -n !NAMESPACE! -o yaml > "output\!NAMESPACE!\job-%%J.yaml" 2>nul
        if not errorlevel 1 (
            echo   - job-%%J.yaml
        )
    )
)

echo Completed extraction for namespace: !NAMESPACE!

:: Create namespace-level images YAML file
echo [!NAMESPACE!] Creating images summary...
set "IMAGES_YAML=output\!NAMESPACE!\!NAMESPACE!-images.yaml"
if exist "!IMAGES_YAML!" del "!IMAGES_YAML!"

echo deployments: > "!IMAGES_YAML!"

:: Process each deployment to extract container images
for /f "tokens=1" %%D in ('kubectl get deployments -n !NAMESPACE! -o custom-columns^=NAME:.metadata.name --no-headers 2^>nul') do (
    echo - name: %%D >> "!IMAGES_YAML!"
    echo   containers: >> "!IMAGES_YAML!"
    
    :: Get container names and images from deployment
    for /f "tokens=*" %%C in ('kubectl get deployment %%D -n !NAMESPACE! -o jsonpath^="{range .spec.template.spec.containers[*]}{.name}{'|'}{.image}{'\n'}{end}" 2^>nul') do (
        if not "%%C"=="" (
            for /f "tokens=1,2 delims=|" %%N in ("%%C") do (
                echo   - name: %%N >> "!IMAGES_YAML!"
                echo     image: %%O >> "!IMAGES_YAML!"
            )
        )
    )
)

:: Process StatefulSets
for /f "tokens=1" %%T in ('kubectl get statefulsets -n !NAMESPACE! -o custom-columns^=NAME:.metadata.name --no-headers 2^>nul') do (
    echo - name: %%T >> "!IMAGES_YAML!"
    echo   containers: >> "!IMAGES_YAML!"
    
    for /f "tokens=*" %%C in ('kubectl get statefulset %%T -n !NAMESPACE! -o jsonpath^="{range .spec.template.spec.containers[*]}{.name}{'|'}{.image}{'\n'}{end}" 2^>nul') do (
        if not "%%C"=="" (
            for /f "tokens=1,2 delims=|" %%N in ("%%C") do (
                echo   - name: %%N >> "!IMAGES_YAML!"
                echo     image: %%O >> "!IMAGES_YAML!"
            )
        )
    )
)

:: Process DaemonSets
for /f "tokens=1" %%A in ('kubectl get daemonsets -n !NAMESPACE! -o custom-columns^=NAME:.metadata.name --no-headers 2^>nul') do (
    echo - name: %%A >> "!IMAGES_YAML!"
    echo   containers: >> "!IMAGES_YAML!"
    
    for /f "tokens=*" %%C in ('kubectl get daemonset %%A -n !NAMESPACE! -o jsonpath^="{range .spec.template.spec.containers[*]}{.name}{'|'}{.image}{'\n'}{end}" 2^>nul') do (
        if not "%%C"=="" (
            for /f "tokens=1,2 delims=|" %%N in ("%%C") do (
                echo   - name: %%N >> "!IMAGES_YAML!"
                echo     image: %%O >> "!IMAGES_YAML!"
            )
        )
    )
)

:: Process Jobs
for /f "tokens=1" %%J in ('kubectl get jobs -n !NAMESPACE! -o custom-columns^=NAME:.metadata.name --no-headers 2^>nul') do (
    echo - name: %%J >> "!IMAGES_YAML!"
    echo   containers: >> "!IMAGES_YAML!"
    
    for /f "tokens=*" %%C in ('kubectl get job %%J -n !NAMESPACE! -o jsonpath^="{range .spec.template.spec.containers[*]}{.name}{'|'}{.image}{'\n'}{end}" 2^>nul') do (
        if not "%%C"=="" (
            for /f "tokens=1,2 delims=|" %%N in ("%%C") do (
                echo   - name: %%N >> "!IMAGES_YAML!"
                echo     image: %%O >> "!IMAGES_YAML!"
            )
        )
    )
)

if exist "!IMAGES_YAML!" (
    echo   - !NAMESPACE!-images.yaml
)
echo.

:: Move to next namespace
shift
goto process_namespaces

:cluster_resources
echo ========================================
echo Extracting Cluster-Wide Resources
echo ========================================
echo.

:: Create cluster-wide directory if it doesn't exist
if not exist "output\cluster-wide" mkdir "output\cluster-wide"

:: Extract StorageClasses as individual files
echo Extracting StorageClasses...
for /f "tokens=1" %%S in ('kubectl get storageclasses -o custom-columns^=NAME:.metadata.name --no-headers 2^>nul') do (
    if not "%%S"=="" (
        kubectl get storageclass %%S -o yaml > "output\cluster-wide\storageclass-%%S.yaml" 2>nul
        if not errorlevel 1 (
            echo   - storageclass-%%S.yaml
        )
    )
)

:: Extract PersistentVolumes as individual files
echo Extracting PersistentVolumes...
for /f "tokens=1" %%P in ('kubectl get pv -o custom-columns^=NAME:.metadata.name --no-headers 2^>nul') do (
    if not "%%P"=="" (
        kubectl get pv %%P -o yaml > "output\cluster-wide\pv-%%P.yaml" 2>nul
        if not errorlevel 1 (
            echo   - pv-%%P.yaml
        )
    )
)

:: Extract ClusterRoles as individual files
echo Extracting ClusterRoles...
for /f "tokens=1" %%R in ('kubectl get clusterroles -o custom-columns^=NAME:.metadata.name --no-headers 2^>nul') do (
    if not "%%R"=="" (
        kubectl get clusterrole %%R -o yaml > "output\cluster-wide\clusterrole-%%R.yaml" 2>nul
        if not errorlevel 1 (
            echo   - clusterrole-%%R.yaml
        )
    )
)

:: Extract ClusterRoleBindings as individual files
echo Extracting ClusterRoleBindings...
for /f "tokens=1" %%B in ('kubectl get clusterrolebindings -o custom-columns^=NAME:.metadata.name --no-headers 2^>nul') do (
    if not "%%B"=="" (
        kubectl get clusterrolebinding %%B -o yaml > "output\cluster-wide\clusterrolebinding-%%B.yaml" 2>nul
        if not errorlevel 1 (
            echo   - clusterrolebinding-%%B.yaml
        )
    )
)

:: Extract Nodes as individual files
echo Extracting Nodes...
for /f "tokens=1" %%N in ('kubectl get nodes -o custom-columns^=NAME:.metadata.name --no-headers 2^>nul') do (
    if not "%%N"=="" (
        kubectl get node %%N -o yaml > "output\cluster-wide\node-%%N.yaml" 2>nul
        if not errorlevel 1 (
            echo   - node-%%N.yaml
        )
    )
)

echo.
echo ========================================
echo Extraction Complete!
echo ========================================
echo.
echo Results saved in: output\
echo   - Per-namespace resources: output\^<namespace^>\
echo   - Cluster-wide resources: output\cluster-wide\
echo.

goto :end

:end
endlocal
echo.
<div dir="rtl">

# מדריך צעד-אחר-צעד: Event Album על Windows

המדריך הזה לוקח אותך מהרצה ראשונה ב-Docker Compose ועד Kubernetes מקומי עם GitOps, סקיילינג אוטומטי, ניטור ובדיקת עומס - **הכל בחינם**, על המחשב שלך.
כל הפקודות הן ל-PowerShell, ומריצים אותן מתיקיית השורש של הריפו (`event-album`).

בכל שלב יש פסקה קצרה **"מה לומדים כאן"** - הרעיון של DevOps שהשלב מלמד.

---

## שלב 0: התקנת כלים

פתח PowerShell (לא חייב כמנהל) והתקן:

```powershell
winget install --id Git.Git -e
winget install --id Docker.DockerDesktop -e
winget install --id k3d.k3d -e
winget install --id Kubernetes.kubectl -e
winget install --id Cloudflare.cloudflared -e
```

אחרי ההתקנה:

1. הפעל את **Docker Desktop** וחכה ל-"Engine running". (הוא משתמש ב-WSL2; אם הוא מבקש להתקין/לעדכן WSL - אשר.)
2. ב-Docker Desktop ← Settings ← Resources: ודא שיש לפחות **6GB זיכרון** (ב-WSL2 ברירת המחדל היא חצי מה-RAM, בדרך כלל מספיק).
3. **פתח חלון PowerShell חדש** כדי שהפקודות החדשות יזוהו.
4. אם PowerShell חוסם הרצת סקריפטים (`running scripts is disabled`), הרץ פעם אחת:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

5. שכפל את הריפו:

```powershell
git clone https://github.com/Itay-nomis/event-album.git
cd event-album
```

**מה לומדים כאן:** סביבת עבודה ניתנת לשחזור. כל הכלים מותקנים מפקודה אחת (winget) עם גרסאות ידועות - בדיוק כמו שמכינים שרת או runner של CI, ולא "התקנתי משהו פעם ואני לא זוכר מה".

---

## שלב 1: הרצה עם Docker Compose

```powershell
copy .env.example .env
docker compose up --build
```

- האפליקציה: http://localhost:8000 - צור אירוע, תראה QR.
- מדדים של ה-worker: http://localhost:9100/metrics
- בדיקות בריאות: http://localhost:8000/healthz ו-http://localhost:8000/readyz
- עצירה: `Ctrl+C`, ואז `docker compose down` (הוסף `-v` כדי למחוק גם את הנתונים).

**מה לומדים כאן:** קונטיינרים ו-Compose. כל שירות (API, worker, Postgres, Redis, SeaweedFS) רץ בקונטיינר נפרד עם גרסה נעולה (לא `latest`), וה-healthchecks קובעים מתי שירות "מוכן". זה אותו image שירוץ אחר כך ב-Kubernetes.

### בדיקה מהטלפון עם קישור ציבורי (גם עם Compose)

בחלון PowerShell נוסף:

```powershell
.\scripts\tunnel.ps1 -Port 8000
```

יופיע קישור בסגנון `https://random-words.trycloudflare.com`. **פתח את הקישור הזה במחשב** (לא localhost!), צור אירוע, וסרוק את ה-QR מהטלפון - עדיף על גלישה סלולרית, כדי לראות שזה באמת ציבורי.

---

## שלב 2: מיזוג הענף ל-main

ArgoCD (שלב 4) מסנכרן את מה שנמצא ב-**main** ב-GitHub. לכן קודם ממזגים את `feature/platform`:

1. ב-GitHub: **Pull requests ← New pull request**, base: `main`, compare: `feature/platform`.
2. חכה שה-CI (הלשונית **Checks**) יהיה ירוק, עבור על השינויים, ואז **Merge**.

**מה לומדים כאן:** Pull Request כשער איכות. ה-CI רץ על ה-PR לפני שמשהו נכנס ל-main: lint, בדיקות, build של ה-images, ובדיקת תקינות של Terraform ו-YAML של Kubernetes. main תמיד "ראוי לפריסה".

---

## שלב 3: ה-CI הראשון על main, ופתיחת ה-images לציבור

אחרי המיזוג ה-CI רץ על main ועושה עוד שני דברים (לשונית **Actions** בריפו):

1. דוחף את שני ה-images ל-GHCR עם תג = ה-SHA של הקומיט:
   `ghcr.io/itay-nomis/event-album-api:<sha>` ו-`ghcr.io/itay-nomis/event-album-worker:<sha>`
2. עושה קומיט אוטומטי (`deploy: ... [skip ci]`) שמעדכן את התג בקובץ `k8s/overlays/local/kustomization.yaml`.

**פעם אחת בלבד - הפוך את ה-packages לציבוריים** (אחרת ה-cluster לא יוכל למשוך אותם):
GitHub ← הפרופיל שלך ← **Packages** ← `event-album-api` ← **Package settings** ← Danger Zone ← **Change visibility** ← Public. אותו דבר ל-`event-album-worker`.

> אם הגדרת הגנה (branch protection) על main, אפשר ל-`github-actions` לדחוף אליו, אחרת קומיט התג ייכשל.

**מה לומדים כאן:** Continuous Integration ו-artifact registry. כל image מתויג ב-SHA של הקוד שממנו נבנה - תג שלא משתנה, כך שתמיד יודעים בדיוק מה רץ, ואפשר לחזור לכל גרסה. (תג כמו `latest` זז ולא אומר כלום.)

---

## שלב 4: Kubernetes מקומי עם ArgoCD

```powershell
git pull
.\scripts\local-up.ps1
```

**הרצה ראשונה לפני שה-CI דחף images?** (למשל אם עוד לא מיזגת) הרץ עם `-LocalImages`: הסקריפט בונה את ה-images אצלך ומייבא אותם ל-cluster בדיוק בשם ובתג ש-ArgoCD יחפש, כך שלא צריך GHCR בכלל:

```powershell
.\scripts\local-up.ps1 -LocalImages
```

(חשוב: `git pull` לפני, כדי שהתג אצלך יהיה זהה לתג ב-main ב-GitHub.)

**ריפו פרטי?** צור ב-GitHub ← Settings ← Developer settings ← **Fine-grained token** עם הרשאת *Contents: Read-only* לריפו הזה בלבד, והרץ:

```powershell
.\scripts\local-up.ps1 -GitHubToken github_pat_XXXX
```

הטוקן נשמר כ-Secret של ArgoCD בתוך ה-cluster בלבד. ריפו ציבורי לא צריך כלום.

מה הסקריפט עושה:

1. בודק שהכלים מותקנים (ומדפיס פקודת winget אם חסר משהו).
2. יוצר cluster של k3d בשם `event-album` - Kubernetes אמיתי (k3s) בתוך Docker - ומחבר את פורט 8080 במחשב ל-Traefik (ה-ingress).
3. יוצר את ה-Secrets (סיסמת Postgres אקראית, סיסמת Grafana) - **הם אף פעם לא נכנסים ל-git**.
4. מתקין ArgoCD ומחיל את `argocd/root-app.yaml`. מכאן ArgoCD מתקין לבד את KEDA, את Prometheus/Grafana ואת האפליקציה.
5. מחכה ומדפיס כתובות וסיסמאות.

ההרצה הראשונה לוקחת 5-10 דקות (הורדת images). בזמן הזה כדאי להסתכל:

```powershell
kubectl get pods -A                       # כל הפודים בכל ה-namespaces
kubectl -n argocd get applications        # מצב הסנכרון של כל אפליקציה
kubectl -n event-album get pods,svc,ingress,hpa,scaledobject
```

האפליקציה: http://localhost:8080

כמה פקודות שכדאי לנסות:

```powershell
kubectl -n event-album logs deploy/api -f          # לוגים חיים
kubectl -n event-album describe pod -l app=event-album-api   # probes, events, משאבים
kubectl -n event-album get configmap,secret
```

**מה לומדים כאן:** אובייקטים של Kubernetes (Deployment, Service, Ingress, StatefulSet עם דיסק קבוע, ConfigMap/Secret) ו-Kustomize: `k8s/base` זהה לכל סביבה, ו-overlays (`local`, `azure`) מוסיפים רק את מה ששונה. ה-probes (`/healthz`, `/readyz`) אומרים ל-Kubernetes מתי להפעיל מחדש פוד ומתי לשלוח אליו תעבורה.

---

## שלב 5: קישור ציבורי ובדיקה מהטלפון

```powershell
.\scripts\tunnel.ps1
```

1. העתק את הכתובת `https://....trycloudflare.com` שמופיעה בפלט.
2. פתח אותה **במחשב** וצור אירוע. ה-QR יצביע על הכתובת הציבורית (האפליקציה מזהה אותה מה-headers שמגיעים דרך ה-tunnel).
3. סרוק את ה-QR בטלפון (על סלולרי), העלה תמונה.
4. במחשב פתח את מסך האולם: `https://....trycloudflare.com/e/<CODE>/show` - התמונה תופיע תוך שניות.

הכתובת משתנה בכל הפעלה ונעלמת כשסוגרים (`Ctrl+C`). זה מיועד לבדיקות, לא לאירוע אמיתי.

**מה לומדים כאן:** reverse proxies ו-ingress. הבקשה עוברת טלפון ← Cloudflare ← cloudflared ← Traefik ← API. כל שכבה מעבירה את הכתובת המקורית ב-headers (`Host`, `X-Forwarded-Proto`), ולכן האפליקציה צריכה "לסמוך" עליהם (`--proxy-headers` ב-uvicorn, `forwardedHeaders` ב-Traefik) כדי לבנות קישורים נכונים.

---

## שלב 6: זרימת GitOps מקצה לקצה

נסה שינוי קטן ותראה אותו מגיע לבד ל-cluster:

1. צור ענף, שנה טקסט ב-`api/app/static/upload.html`, דחוף ופתח PR.
2. ה-CI רץ על ה-PR. מזג.
3. ה-CI על main: בונה images ← דוחף ל-GHCR ← קומיט שמעדכן את התג ב-`k8s/overlays/local`.
4. ArgoCD בודק את הריפו כל ~3 דקות (או לחץ **Refresh** ב-UI) ← רואה תג חדש ← מגלגל את הפודים.

פתיחת ה-UI של ArgoCD:

```powershell
kubectl -n argocd port-forward svc/argocd-server 8081:443
```

ואז https://localhost:8081 (אשר את אזהרת התעודה), משתמש `admin`. הסיסמה:

```powershell
$p = kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}"
[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($p))
```

ניסוי "self-heal": מחק משהו ידנית ותראה את ArgoCD מחזיר אותו:

```powershell
kubectl -n event-album delete deployment api
kubectl -n event-album get deploy -w
```

Rollback = `git revert` לקומיט ה-deploy, ו-ArgoCD מחזיר את הגרסה הקודמת.

**מה לומדים כאן:** GitOps. אף אחד לא מריץ `kubectl apply` על סביבה - ה-git הוא מקור האמת, ו-ArgoCD דואג שה-cluster יהיה זהה לו (כולל תיקון "סטיות" שמישהו עשה ידנית). כל שינוי בסביבה הוא קומיט עם היסטוריה, review ואפשרות revert. דפוס app-of-apps נותן אפליקציה אחת שמנהלת את כל השאר.

---

## שלב 7: בדיקת עומס - חתונה של 300 אורחים

פתח שלושה חלונות PowerShell:

```powershell
# חלון 1: הפודים - תראה workers נוספים ונעלמים
kubectl -n event-album get pods -w

# חלון 2: ה-autoscalers
kubectl -n event-album get hpa -w

# חלון 3: הבדיקה עצמה (k6 ב-Docker, אין מה להתקין)
.\loadtest\run-loadtest.ps1 -TimeScale 0.1    # דקה אחת לחימום
.\loadtest\run-loadtest.ps1                   # הערב המלא: ~900 תמונות ב-10 דקות
```

מה אמור לקרות:

1. ה-worker מתחיל ב-**0** פודים. ברגע שיש עבודה בתור, KEDA מעיר פוד אחד.
2. ב"ריקוד הראשון" (שיא באמצע) התור גדל, ו-KEDA מוסיף workers (יעד: 5 עבודות ממתינות לכל worker, עד 10).
3. בסוף התור מתרוקן, ואחרי כ-2 דקות ה-workers יורדים חזרה ל-0. כל worker מסיים את התמונה שבידו לפני שהוא יוצא (טיפול ב-SIGTERM).
4. בסיכום של k6: `http_req_failed` מתחת ל-1% ו-p95 של העלאה מתחת ל-5 שניות.

**מה לומדים כאן:** סקיילינג מונחה-אירועים. ה-API מוגדל לפי CPU (HPA רגיל), אבל ה-worker מוגדל לפי **אורך התור** (KEDA) - המדד שבאמת משקף עבודה ממתינה - כולל ירידה ל-0 כשאין אירוע. ו-graceful shutdown הוא מה שמאפשר להקטין בלי לאבד עבודה.

---

## שלב 8: ניטור והתראות - Grafana ו-Prometheus

```powershell
kubectl -n monitoring port-forward svc/kube-prometheus-stack-grafana 3000:80
```

http://localhost:3000, משתמש `admin`, הסיסמה:

```powershell
$p = kubectl -n monitoring get secret grafana-admin -o jsonpath="{.data.admin-password}"
[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($p))
```

ב-Dashboards חפש **Event Album**: העלאות לדקה, אורך התור מול מספר ה-workers, זמן עיבוד p95, latency של ה-API, בקשות לפי סטטוס. הרץ את בדיקת העומס ותסתכל על הגרפים.

התראות: פתח את Prometheus והלשונית **Alerts**:

```powershell
kubectl -n monitoring port-forward svc/kube-prometheus-stack-prometheus 9090:9090
```

ההתראות שלנו (קובץ `k8s/components/monitoring/alerts.yaml`): API למטה, יותר מ-5% שגיאות 5xx, תור תקוע מעל 50 במשך 5 דקות, worker שנכשל בעיבוד. נסה להפיל משהו:

```powershell
kubectl -n event-album scale deployment redis --replicas=0   # ה-readiness של ה-API ייכשל
# ...תסתכל על /readyz, על הפודים ועל ה-Alerts, ואז החזר:
kubectl -n event-album scale deployment redis --replicas=1
```

(סביר ש-ArgoCD יחזיר את Redis לבד תוך שניות - זה ה-self-heal משלב 6. כדי שהניסוי יחזיק מספיק זמן לראות התראה, כבה זמנית את ה-Auto-Sync של האפליקציה `event-album` ב-UI של ArgoCD, ואל תשכח להדליק בחזרה.)

**מה לומדים כאן:** Observability. האפליקציה חושפת מדדים (`/metrics`), Prometheus אוסף אותם לפי ServiceMonitor/PodMonitor, Grafana מציגה, ו-PrometheusRule מגדיר מתי מתריעים. הכל מוגדר כקוד בריפו ונפרס ב-GitOps, כולל ה-dashboard.

---

## שלב 9 (בהמשך): Azure

הקוד נמצא ב-`infra/azure` (Terraform): AKS (שכבה חינמית, node אחד, workload identity, KEDA כ-add-on), Blob Storage, Postgres Flexible Server הכי קטן, והתראת תקציב למייל.
**זה עולה כסף.** ה-CI רק בודק שהקוד תקין ולא יוצר כלום. כשתגיע לזה:

1. פתח חשבון Azure חדש עם קרדיט הניסיון (200 דולר ל-30 יום).
2. עקוב אחרי `infra/azure/README.md` - `terraform plan`, לקרוא מה ייווצר, `terraform apply`.
3. כשמסיימים - **תמיד** `terraform destroy`.

**מה לומדים כאן:** Infrastructure as Code ו-FinOps בסיסי. כל התשתית מתוארת בקוד, נבדקת ב-CI כמו קוד, נוצרת ונמחקת בפקודה אחת - ותקציב עם התראה שומר עליך מהפתעות. workload identity מראה איך פוד ניגש ל-Azure **בלי שום מפתח או סיסמה**.

---

## ניקוי

```powershell
.\scripts\local-down.ps1          # מוחק את ה-cluster וכל מה שבו
k3d cluster stop event-album      # או: רק לעצור ולשמור נתונים
docker compose down -v            # ניקוי של Compose
```

## איפה הסודות?

| סוד | איפה | מי יוצר |
|---|---|---|
| סיסמאות של Compose | `.env` (ב-gitignore) | אתה, מ-`.env.example` |
| `event-album-secrets` (סיסמת Postgres, `DATABASE_URL`) | Secret ב-namespace `event-album` | `local-up.ps1` - אקראי, או מ-`k8s/overlays/local/secrets.env` (ב-gitignore) |
| `grafana-admin` | Secret ב-namespace `monitoring` | `local-up.ps1` |
| טוקן GitHub לריפו פרטי | Secret ב-namespace `argocd` | `local-up.ps1 -GitHubToken` |
| דחיפה ל-GHCR מה-CI | `GITHUB_TOKEN` אוטומטי של GitHub Actions | GitHub |

ArgoCD לא מוחק את ה-Secrets האלה כי הם לא ב-git. (השלב הבא בתחום: Sealed Secrets או External Secrets.)

## תקלות נפוצות

| תסמין | מה לעשות |
|---|---|
| `port is already allocated` ל-8080 | הרץ `.\scripts\local-up.ps1 -HttpPort 8090` ופתח localhost:8090 (ו-`tunnel.ps1 -Port 8090`) |
| פודים ב-`ImagePullBackOff` | ה-CI עוד לא דחף images, או שה-packages פרטיים. הרץ עם `-LocalImages`, או הפוך את ה-packages ל-Public (שלב 3) |
| ArgoCD מראה `Unknown`/`ComparisonError` | הריפו פרטי בלי טוקן (`-GitHubToken`), או שהענף עוד לא מוזג ל-main |
| `event-album` נשאר `OutOfSync` בהתחלה | נורמלי בדקות הראשונות: הוא מחכה ש-KEDA ו-Prometheus יתקינו את ה-CRDs, ומנסה שוב לבד |
| הכל איטי / פודים ב-`Pending` | הגדל זיכרון ל-Docker Desktop (לפחות 6GB) |
| ה-QR מצביע על localhost | פתחת את האתר ב-localhost. פתח את כתובת ה-trycloudflare במחשב ואז צור את האירוע |

</div>

# DataScope - AI-Powered BigQuery Data Profiling Dashboard

**DataScope** is a comprehensive data profiling dashboard for Verily Workbench that automatically discovers and analyzes BigQuery datasets in your workspace.

## Features

### 📊 Multi-Table Support
- Auto-discovers all BigQuery datasets and tables in your workspace
- Easy table selection with row count and size information
- Independent profiling and caching per table

### 📈 Comprehensive Data Profiling
- **Table-Level Metrics**: Total rows, columns, memory usage, duplicate detection
- **Column-Level Analysis**:
  - Numeric columns: Mean, median, std dev, quartiles, skewness, kurtosis, distributions
  - Categorical columns: Unique values, top values with frequencies
  - Datetime columns: Min/max dates, range calculations
- **Interactive Visualizations**: Histograms, correlation matrices, heatmaps
- **Sample Data Preview**: First 100 rows for quick inspection

### 💬 Talk to Your Data (AI-Powered)
- Natural language questions about your data
- **Smart Query Routing**:
  - Instant answers from aggregate statistics (no API cost)
  - AI analysis via OpenAI GPT-4o for complex questions
- Visual badges showing answer method (📊 Instant Answer vs 🤖 AI Analysis)
- Powered by OpenAI with secure API key retrieval from Secret Manager

### ⚡ Performance Optimized
- Per-table caching for instant switching
- Chat uses 100 sample rows + aggregate statistics from 1,000 rows
- Smart pattern detection for 8 common statistical queries

## Deployment

### Prerequisites
1. Verily Workbench workspace with BigQuery datasets
2. OpenAI API key stored in Google Cloud Secret Manager (optional, for chat feature)
   - Project: `wb-smart-cabbage-5940`
   - Secret name: `si-ops-openai-api-key`

### Deploy to Workbench

1. **In Workbench UI**, create a new custom app:
   - Navigate to your workspace
   - Click "Create App" → "Custom App"
   - Enter the following details:

2. **App Configuration**:
   - **Repository URL**: `https://github.com/SIVerilyDP/workbench-app-devcontainers.git`
   - **Branch**: `main`
   - **Folder Path**: `src/datascope`
   - **App Name**: Choose a name (e.g., "DataScope - Prod")

3. **Start the app**:
   - Workbench will build the Docker container
   - Once running, click the app link to access

### Access URL Format
```
https://workbench.verily.com/app/[APP_UUID]/proxy/8080/
```

Get your app UUID:
```bash
wb app list --format=json | jq -r '.[] | select(.status == "RUNNING") | .id' | head -1
```

## Usage

### 1. Select a Table
- Use the dropdown to select any BigQuery table from your workspace
- Table info (dataset, rows, size) displays automatically

### 2. Explore Data
Navigate through 6 tabs:
- **Overview**: Table-level metrics and summary statistics
- **Column Details**: Detailed profiling for each column with visualizations
- **Correlation**: Interactive correlation matrix for numeric columns
- **Heatmap**: Visual correlation heatmap
- **Sample Data**: Preview first 100 rows
- **💬 Talk to Your Data**: Ask questions in natural language

### 3. Ask Questions
Examples of questions you can ask:
- "What is the average billing amount?" → Instant Answer
- "How many unique patients are there?" → Instant Answer
- "What's the most common diagnosis?" → Instant Answer
- "What patterns do you see in the data?" → AI Analysis
- "Are there any concerning outliers?" → AI Analysis

## Smart Query Routing

DataScope intelligently routes your questions:

### Instant Answers (No API Cost) 📊
- Average/mean/median calculations
- Maximum/minimum values
- Most common values
- Unique counts
- Standard deviation
- Specific value counts
- Range queries

### AI Analysis (OpenAI GPT-4o) 🤖
- Complex pattern detection
- Multi-column insights
- Trend analysis
- Recommendations
- Outlier explanations

## Architecture

### Tech Stack
- **Backend**: Flask 3.0 with CORS
- **Data Processing**: Pandas, NumPy, SciPy
- **Visualization**: Plotly, Matplotlib, Seaborn
- **Cloud Integration**: Google Cloud BigQuery, Storage, Secret Manager
- **AI**: OpenAI GPT-4o with US regional endpoint
- **Frontend**: Vanilla JavaScript with Plotly.js

### Security
- API keys retrieved securely from Google Cloud Secret Manager
- No credentials stored in code or environment variables
- Workspace-level access control via Workbench

### Performance
- Smart caching: Per-table data caching for instant switching
- Efficient sampling: 100-row samples for chat, aggregate stats from 1,000 rows
- Lazy loading: Profile data loaded only when table selected

## Development

### Local Testing (with Docker)
```bash
cd src/datascope

# Create required network
docker network create app-network

# Build and run
docker compose build
docker compose up

# Access at http://localhost:8080
```

### File Structure
```
src/datascope/
├── .devcontainer.json          # Workbench devcontainer config
├── docker-compose.yaml         # Docker Compose configuration
├── Dockerfile                  # Container build instructions
├── devcontainer-template.json  # Template metadata
├── requirements.txt            # Python dependencies
├── app.py                      # Flask backend (main app)
├── templates/
│   └── index.html             # Frontend dashboard
└── README.md                  # This file
```

### Key Files
- **app.py** (~670 lines): Flask server with BigQuery discovery, profiling, smart routing, OpenAI integration
- **index.html** (~1000 lines): Multi-tab dashboard with chat interface

## Configuration

### OpenAI API Key Setup
If you want to use the chat feature with a different project/secret:

Edit `app.py`, lines ~100-110:
```python
def get_openai_client():
    project_id = "YOUR-PROJECT-ID"  # Change this
    secret_name = f"projects/{project_id}/secrets/YOUR-SECRET-NAME/versions/latest"  # Change this
    # ... rest of function
```

### Sample Size Configuration
Chat uses fixed sample sizes for performance. To adjust:

Edit `app.py`, line ~565:
```python
sample_size = request_data.get('sample_size', 1000)  # Change default here
```

## Troubleshooting

### App won't start
- Check `.devcontainer.json` is at repo root (not in a folder)
- Verify `docker-compose.yaml` references `application-server` container
- Ensure `app-network` is external in docker-compose

### Can't access app
- Use correct URL format: `workbench.verily.com/app/UUID/proxy/8080/`
- Verify app status: `wb app list`
- Check Flask binds to `0.0.0.0` (not `localhost`)

### Chat feature errors
- Verify OpenAI API key exists in Secret Manager
- Check project ID and secret name in `app.py`
- Ensure US regional endpoint is configured
- Test Secret Manager access from workspace

### No tables discovered
- Verify BigQuery datasets exist in workspace
- Check environment variables: `env | grep WORKBENCH_`
- Ensure datasets have tables (not empty)

## Support

- **Workbench Docs**: https://support.workbench.verily.com
- **Custom Apps Guide**: https://github.com/verily-src/workbench-app-devcontainers
- **Workbench Support**: support@workbench.verily.com

## Version History

- **v1.0.0** (2026-02-24): Initial release
  - Multi-table BigQuery profiling
  - Comprehensive column-level statistics
  - Interactive visualizations
  - AI-powered chat with smart routing
  - OpenAI GPT-4o integration
  - Performance optimizations

## License

See repository root for license information.

---

**Built for Verily Workbench** | Powered by OpenAI GPT-4o

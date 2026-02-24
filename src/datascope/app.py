from flask import Flask, render_template, jsonify, request
from flask_cors import CORS
from google.cloud import bigquery, storage
from google.cloud import secretmanager
import pandas as pd
import numpy as np
import json
import os
from scipy import stats
import base64
import io
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import seaborn as sns
from openai import OpenAI

app = Flask(__name__)
CORS(app)

# OpenAI client (initialized lazily)
_openai_client = None

# Enhanced cache with table-specific storage
_cache = {
    'tables': {},  # Format: {'project.dataset.table': {'data': df, 'profiling': profile}}
    'current_table': None
}

def get_openai_client():
    """Get OpenAI client with API key from Secret Manager"""
    global _openai_client

    if _openai_client is not None:
        return _openai_client

    try:
        # Retrieve API key from Google Cloud Secret Manager
        project_id = "wb-smart-cabbage-5940"
        secret_name = f"projects/{project_id}/secrets/si-ops-openai-api-key/versions/latest"

        secret_client = secretmanager.SecretManagerServiceClient()
        response = secret_client.access_secret_version(name=secret_name)
        api_key = response.payload.data.decode("UTF-8")

        # Initialize OpenAI client with US regional endpoint
        _openai_client = OpenAI(
            api_key=api_key,
            base_url="https://us.api.openai.com/v1"
        )
        return _openai_client
    except Exception as e:
        print(f"Error initializing OpenAI client: {e}")
        return None

def get_workspace_datasets():
    """Discover all BigQuery datasets in workspace"""
    datasets = []

    # Hardcoded workspace datasets (from CLAUDE.md)
    workspace_datasets = {
        'SI_Sandbox_BQ': 'wb-beady-aubergine-7486.SI_Sandbox_BQ'
    }

    # Also check environment variables as fallback
    for key, value in os.environ.items():
        if key.startswith('WORKBENCH_') and '.' in value and not value.startswith('gs://'):
            try:
                parts = value.split('.')
                if len(parts) == 2:  # project.dataset
                    workspace_datasets[key.replace('WORKBENCH_', '')] = value
            except:
                pass

    # Convert to dataset info list
    for name, path in workspace_datasets.items():
        parts = path.split('.')
        if len(parts) == 2:
            datasets.append({
                'name': name,
                'project': parts[0],
                'dataset': parts[1],
                'full_path': path
            })

    return datasets

def discover_all_tables():
    """Discover all tables across all BigQuery datasets in workspace"""
    client = bigquery.Client()
    all_tables = []

    datasets = get_workspace_datasets()

    for ds_info in datasets:
        try:
            dataset_ref = f"{ds_info['project']}.{ds_info['dataset']}"
            tables = client.list_tables(dataset_ref)

            for table in tables:
                table_full_name = f"{ds_info['project']}.{ds_info['dataset']}.{table.table_id}"

                # Get table metadata
                table_ref = client.get_table(table_full_name)

                all_tables.append({
                    'dataset_name': ds_info['name'],
                    'project': ds_info['project'],
                    'dataset': ds_info['dataset'],
                    'table': table.table_id,
                    'full_name': table_full_name,
                    'num_rows': table_ref.num_rows,
                    'size_mb': round(table_ref.num_bytes / 1024 / 1024, 2) if table_ref.num_bytes else 0,
                    'created': str(table_ref.created) if table_ref.created else None,
                    'modified': str(table_ref.modified) if table_ref.modified else None
                })
        except Exception as e:
            print(f"Error listing tables in {ds_info['name']}: {e}")
            continue

    return all_tables

def load_bigquery_table(table_full_name, limit=None):
    """Load data from a specific BigQuery table"""
    client = bigquery.Client()

    limit_clause = f"LIMIT {limit}" if limit else ""
    query = f"""
    SELECT *
    FROM `{table_full_name}`
    {limit_clause}
    """

    df = client.query(query).to_dataframe()
    return df

def profile_numeric_column(series):
    """Generate detailed profile for numeric column"""
    profile = {
        'type': 'numeric',
        'count': int(series.count()),
        'missing': int(series.isna().sum()),
        'missing_pct': float(series.isna().sum() / len(series) * 100),
        'unique': int(series.nunique()),
        'mean': float(series.mean()) if not series.empty else 0,
        'std': float(series.std()) if not series.empty else 0,
        'min': float(series.min()) if not series.empty else 0,
        'max': float(series.max()) if not series.empty else 0,
        'q25': float(series.quantile(0.25)) if not series.empty else 0,
        'median': float(series.median()) if not series.empty else 0,
        'q75': float(series.quantile(0.75)) if not series.empty else 0,
        'skewness': float(series.skew()) if not series.empty else 0,
        'kurtosis': float(series.kurtosis()) if not series.empty else 0,
    }

    # Generate histogram data
    if not series.empty and series.count() > 0:
        hist, bin_edges = np.histogram(series.dropna(), bins=30)
        profile['histogram'] = {
            'counts': hist.tolist(),
            'bins': bin_edges.tolist()
        }

    return profile

def profile_categorical_column(series):
    """Generate detailed profile for categorical column"""
    value_counts = series.value_counts()

    profile = {
        'type': 'categorical',
        'count': int(series.count()),
        'missing': int(series.isna().sum()),
        'missing_pct': float(series.isna().sum() / len(series) * 100),
        'unique': int(series.nunique()),
        'top_values': []
    }

    # Top 20 most frequent values
    for value, count in value_counts.head(20).items():
        profile['top_values'].append({
            'value': str(value),
            'count': int(count),
            'percentage': float(count / len(series) * 100)
        })

    return profile

def profile_datetime_column(series):
    """Generate detailed profile for datetime column"""
    profile = {
        'type': 'datetime',
        'count': int(series.count()),
        'missing': int(series.isna().sum()),
        'missing_pct': float(series.isna().sum() / len(series) * 100),
        'unique': int(series.nunique()),
        'min': str(series.min()) if not series.empty else None,
        'max': str(series.max()) if not series.empty else None,
    }

    if not series.empty and series.count() > 0:
        try:
            profile['range_days'] = int((series.max() - series.min()).days)
        except:
            pass

    return profile

def generate_correlation_matrix(df):
    """Generate correlation matrix for numeric columns"""
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()

    if len(numeric_cols) < 2:
        return None

    corr_matrix = df[numeric_cols].corr()

    return {
        'columns': numeric_cols,
        'matrix': corr_matrix.values.tolist()
    }

def generate_heatmap(df):
    """Generate heatmap as base64 image"""
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()

    if len(numeric_cols) < 2:
        return None

    plt.figure(figsize=(10, 8))
    corr_matrix = df[numeric_cols].corr()
    sns.heatmap(corr_matrix, annot=True, fmt='.2f', cmap='coolwarm', center=0,
                square=True, linewidths=1, cbar_kws={"shrink": 0.8})
    plt.title('Correlation Heatmap', fontsize=16, pad=20)
    plt.tight_layout()

    buffer = io.BytesIO()
    plt.savefig(buffer, format='png', dpi=100, bbox_inches='tight')
    buffer.seek(0)
    image_base64 = base64.b64encode(buffer.read()).decode()
    plt.close()

    return image_base64

def profile_dataframe(df):
    """Generate comprehensive data profile"""

    # Table-level metrics
    table_profile = {
        'total_rows': int(len(df)),
        'total_columns': int(len(df.columns)),
        'memory_usage_mb': float(df.memory_usage(deep=True).sum() / 1024 / 1024),
        'duplicate_rows': int(df.duplicated().sum()),
        'duplicate_rows_pct': float(df.duplicated().sum() / len(df) * 100) if len(df) > 0 else 0,
    }

    # Column-level profiles
    column_profiles = {}
    for col in df.columns:
        series = df[col]

        if pd.api.types.is_numeric_dtype(series):
            column_profiles[col] = profile_numeric_column(series)
        elif pd.api.types.is_datetime64_any_dtype(series):
            column_profiles[col] = profile_datetime_column(series)
        else:
            column_profiles[col] = profile_categorical_column(series)

    # Correlation matrix
    correlation = generate_correlation_matrix(df)

    # Generate heatmap
    heatmap_image = generate_heatmap(df)

    return {
        'table': table_profile,
        'columns': column_profiles,
        'correlation': correlation,
        'heatmap': heatmap_image
    }

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/discover-tables')
def discover_tables():
    """Discover all tables in all BigQuery datasets"""
    try:
        tables = discover_all_tables()
        return jsonify({
            'tables': tables,
            'count': len(tables)
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/profile')
def get_profile():
    """Get full data profile for a specific table"""
    try:
        # Get table name from query parameter
        table_name = request.args.get('table')

        if not table_name:
            return jsonify({"error": "Table name required. Use ?table=project.dataset.table"}), 400

        # Check cache
        if table_name in _cache['tables'] and _cache['tables'][table_name].get('profiling'):
            return jsonify(_cache['tables'][table_name]['profiling'])

        # Initialize cache for this table
        if table_name not in _cache['tables']:
            _cache['tables'][table_name] = {}

        # Load data if not cached
        if _cache['tables'][table_name].get('data') is None:
            _cache['tables'][table_name]['data'] = load_bigquery_table(table_name)

        df = _cache['tables'][table_name]['data']

        # Generate profile
        profile = profile_dataframe(df)
        _cache['tables'][table_name]['profiling'] = profile
        _cache['current_table'] = table_name

        return jsonify(profile)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/sample-data')
def get_sample_data():
    """Get sample data rows for a specific table"""
    try:
        # Get table name from query parameter
        table_name = request.args.get('table')

        if not table_name:
            return jsonify({"error": "Table name required. Use ?table=project.dataset.table"}), 400

        # Initialize cache for this table
        if table_name not in _cache['tables']:
            _cache['tables'][table_name] = {}

        # Load data if not cached
        if _cache['tables'][table_name].get('data') is None:
            _cache['tables'][table_name]['data'] = load_bigquery_table(table_name)

        df = _cache['tables'][table_name]['data']
        sample = df.head(100).to_dict(orient='records')

        # Convert datetime objects to strings
        for row in sample:
            for key, value in row.items():
                if pd.isna(value):
                    row[key] = None
                elif isinstance(value, (pd.Timestamp, np.datetime64)):
                    row[key] = str(value)

        return jsonify({
            'columns': df.columns.tolist(),
            'data': sample
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/clear-cache')
def clear_cache():
    """Clear all cached data"""
    try:
        table_name = request.args.get('table')

        if table_name and table_name in _cache['tables']:
            del _cache['tables'][table_name]
            return jsonify({"message": f"Cache cleared for {table_name}"})
        else:
            _cache['tables'] = {}
            _cache['current_table'] = None
            return jsonify({"message": "All caches cleared"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def find_column_in_question(question, columns):
    """Find which column the question is asking about"""
    question_lower = question.lower()

    # Try exact matches first
    for col in columns:
        if col.lower() in question_lower:
            return col

    # Try partial matches
    for col in columns:
        col_words = col.lower().split()
        for word in col_words:
            if len(word) > 3 and word in question_lower:
                return col

    return None

def answer_from_statistics(question, context):
    """Try to answer question directly from aggregate statistics"""
    question_lower = question.lower()
    columns = context['columns']

    # Find which column the question is about
    target_column = find_column_in_question(question, columns)

    if not target_column:
        return None

    # Pattern 1: Average/Mean
    if any(word in question_lower for word in ['average', 'mean', 'avg']):
        if target_column in context['numeric_statistics']:
            stats = context['numeric_statistics'][target_column]
            return {
                'answer': f"The average {target_column} is {stats['mean']:.2f} (based on {stats['count']:,} values from the sample of {context['sample_size']:,} rows).",
                'method': 'statistics',
                'stat_used': f"mean of {target_column}"
            }

    # Pattern 2: Maximum/Highest
    if any(word in question_lower for word in ['maximum', 'max', 'highest', 'largest']):
        if target_column in context['numeric_statistics']:
            stats = context['numeric_statistics'][target_column]
            return {
                'answer': f"The maximum {target_column} is {stats['max']:.2f} (from a sample of {context['sample_size']:,} rows).",
                'method': 'statistics',
                'stat_used': f"max of {target_column}"
            }

    # Pattern 3: Minimum/Lowest
    if any(word in question_lower for word in ['minimum', 'min', 'lowest', 'smallest']):
        if target_column in context['numeric_statistics']:
            stats = context['numeric_statistics'][target_column]
            return {
                'answer': f"The minimum {target_column} is {stats['min']:.2f} (from a sample of {context['sample_size']:,} rows).",
                'method': 'statistics',
                'stat_used': f"min of {target_column}"
            }

    # Pattern 4: Most common/frequent/prescribed
    if any(word in question_lower for word in ['most common', 'most frequent', 'most prescribed', 'top', 'most popular']):
        if target_column in context['categorical_statistics']:
            stats = context['categorical_statistics'][target_column]
            if stats['top_values']:
                top = stats['top_values'][0]
                answer = f"The most common {target_column} is '{top['value']}' with {top['count']:,} occurrences ({top['percentage']:.1f}% of the sample)."

                # Add top 5 if available
                if len(stats['top_values']) > 1:
                    answer += "\n\nTop 5:\n"
                    for i, item in enumerate(stats['top_values'][:5], 1):
                        answer += f"{i}. {item['value']}: {item['count']:,} ({item['percentage']:.1f}%)\n"

                return {
                    'answer': answer,
                    'method': 'statistics',
                    'stat_used': f"top values of {target_column}"
                }

    # Pattern 5: How many unique/distinct
    if any(phrase in question_lower for phrase in ['how many unique', 'how many different', 'unique count', 'distinct']):
        if target_column in context['categorical_statistics']:
            stats = context['categorical_statistics'][target_column]
            return {
                'answer': f"There are {stats['unique_count']:,} unique values in the {target_column} column (from a sample of {context['sample_size']:,} rows).",
                'method': 'statistics',
                'stat_used': f"unique count of {target_column}"
            }

    # Pattern 6: Count of specific value (e.g., "how many patients have diabetes")
    if 'how many' in question_lower or 'count' in question_lower:
        if target_column in context['categorical_statistics']:
            stats = context['categorical_statistics'][target_column]
            # Try to find the specific value mentioned
            for item in stats['top_values']:
                if item['value'].lower() in question_lower:
                    return {
                        'answer': f"There are {item['count']:,} rows where {target_column} is '{item['value']}' ({item['percentage']:.1f}% of the {context['sample_size']:,} row sample).",
                        'method': 'statistics',
                        'stat_used': f"count of specific value in {target_column}"
                    }

    # Pattern 7: Median
    if 'median' in question_lower:
        if target_column in context['numeric_statistics']:
            stats = context['numeric_statistics'][target_column]
            return {
                'answer': f"The median {target_column} is {stats['median']:.2f} (based on {stats['count']:,} values from the sample of {context['sample_size']:,} rows).",
                'method': 'statistics',
                'stat_used': f"median of {target_column}"
            }

    # Pattern 8: Standard deviation
    if any(word in question_lower for word in ['standard deviation', 'std dev', 'std', 'variance', 'variability']):
        if target_column in context['numeric_statistics']:
            stats = context['numeric_statistics'][target_column]
            return {
                'answer': f"The standard deviation of {target_column} is {stats['std']:.2f} (mean: {stats['mean']:.2f}, based on {stats['count']:,} values).",
                'method': 'statistics',
                'stat_used': f"std of {target_column}"
            }

    return None

@app.route('/api/ask', methods=['POST'])
def ask_question():
    """Ask a question about the data using smart routing (statistics or OpenAI)"""
    try:
        data = request.get_json()
        question = data.get('question')
        table_name = data.get('table')
        sample_size = data.get('sample_size', 1000)  # Default 1000 rows

        if not question:
            return jsonify({"error": "Question is required"}), 400

        if not table_name:
            return jsonify({"error": "Table name is required"}), 400

        # Get OpenAI client
        client = get_openai_client()
        if client is None:
            return jsonify({"error": "OpenAI client not available"}), 500

        # Initialize cache for this table
        if table_name not in _cache['tables']:
            _cache['tables'][table_name] = {}

        # Load data - use cached data if available, otherwise load from BigQuery
        if _cache['tables'][table_name].get('data') is None:
            # Load with a reasonable limit (max 50k rows to avoid memory issues)
            max_limit = 50000 if sample_size == 0 else max(sample_size, 10000)
            _cache['tables'][table_name]['data'] = load_bigquery_table(table_name, limit=max_limit)

        df = _cache['tables'][table_name]['data']

        # Check if we need to reload with more data
        if sample_size > 0 and len(df) < sample_size:
            # Need more data, reload
            _cache['tables'][table_name]['data'] = load_bigquery_table(table_name, limit=sample_size)
            df = _cache['tables'][table_name]['data']

        # Sample from the loaded data
        if sample_size > 0 and len(df) > sample_size:
            df_sample = df.sample(n=min(sample_size, len(df)), random_state=42)
        else:
            df_sample = df

        # Prepare sample data (convert datetime and handle NaN) - use more rows
        sample_rows_to_show = min(100, len(df_sample))  # Show up to 100 rows
        sample_data_raw = df_sample.head(sample_rows_to_show).to_dict(orient='records')
        sample_data_clean = []

        for row in sample_data_raw:
            clean_row = {}
            for key, value in row.items():
                if pd.isna(value):
                    clean_row[key] = None
                elif isinstance(value, (pd.Timestamp, np.datetime64)):
                    clean_row[key] = str(value)
                elif isinstance(value, (np.integer, np.floating)):
                    clean_row[key] = float(value)
                else:
                    clean_row[key] = str(value)
            sample_data_clean.append(clean_row)

        # Prepare context for OpenAI
        context = {
            "table_name": table_name,
            "total_rows": len(df),
            "sample_size": len(df_sample),
            "columns": df.columns.tolist(),
            "data_types": {k: str(v) for k, v in df.dtypes.to_dict().items()},
            "sample_data": sample_data_clean,
            "numeric_statistics": {},
            "categorical_statistics": {}
        }

        # Add statistics for numeric columns
        numeric_cols = df_sample.select_dtypes(include=[np.number]).columns
        for col in numeric_cols:
            col_data = df_sample[col].dropna()
            if len(col_data) > 0:
                context["numeric_statistics"][col] = {
                    "count": int(len(col_data)),
                    "mean": float(col_data.mean()),
                    "median": float(col_data.median()),
                    "min": float(col_data.min()),
                    "max": float(col_data.max()),
                    "std": float(col_data.std())
                }

        # Add statistics for categorical columns (top 10 values with counts)
        categorical_cols = df_sample.select_dtypes(include=['object']).columns
        for col in categorical_cols:
            value_counts = df_sample[col].value_counts().head(10)
            context["categorical_statistics"][col] = {
                "unique_count": int(df_sample[col].nunique()),
                "top_values": [
                    {"value": str(val), "count": int(count), "percentage": round(count / len(df_sample) * 100, 2)}
                    for val, count in value_counts.items()
                ]
            }

        # SMART ROUTING: Try to answer from statistics first
        stats_answer = answer_from_statistics(question, context)
        if stats_answer:
            # We can answer this directly from statistics!
            return jsonify({
                "answer": stats_answer['answer'],
                "sample_size_used": len(df_sample),
                "total_rows": len(df),
                "method": stats_answer['method'],
                "stat_used": stats_answer.get('stat_used')
            })

        # If we can't answer from stats, use OpenAI
        # Build the prompt
        system_prompt = """You are a data analyst assistant. You help users understand and analyze their healthcare data.

Provide clear, accurate answers based on the data provided. When referencing numbers, be specific.
If the question cannot be answered with the available data, explain what's missing.
Keep responses concise but informative."""

        user_prompt = f"""I have a healthcare dataset with the following information:

Table: {context['table_name']}
Total rows in database: {context['total_rows']:,}
Sample size analyzed: {context['sample_size']:,}
Columns: {', '.join(context['columns'])}

Sample data (first {sample_rows_to_show} rows):
{json.dumps(context['sample_data'], indent=2, default=str)}

NUMERIC COLUMN STATISTICS (from full {context['sample_size']:,} row sample):
{json.dumps(context['numeric_statistics'], indent=2)}

CATEGORICAL COLUMN STATISTICS (from full {context['sample_size']:,} row sample):
Top values and frequencies for each categorical column:
{json.dumps(context['categorical_statistics'], indent=2)}

User Question: {question}

Please provide a detailed answer based on this data. Note: You have access to statistics computed from the FULL sample of {context['sample_size']:,} rows, not just the {sample_rows_to_show} sample rows shown above."""

        # Call OpenAI API (using latest model gpt-4o)
        response = client.chat.completions.create(
            model="gpt-4o",  # Latest OpenAI model
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.7,
            max_tokens=1000
        )

        answer = response.choices[0].message.content

        return jsonify({
            "answer": answer,
            "sample_size_used": len(df_sample),
            "total_rows": len(df),
            "method": "ai",
            "model": "gpt-4o"
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/status')
def get_status():
    """Get profiling status"""
    return jsonify({
        'status': 'ready',
        'cached_tables': list(_cache['tables'].keys()),
        'current_table': _cache.get('current_table')
    })

if __name__ == '__main__':
    # CRITICAL: host='0.0.0.0' required for Workbench proxy access
    app.run(host='0.0.0.0', port=8080, debug=False, threaded=True)

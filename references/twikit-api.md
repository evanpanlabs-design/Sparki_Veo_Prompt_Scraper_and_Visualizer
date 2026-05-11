# twikit API Reference for X Scraper

## Authentication

### Login
```python
from twikit import Client

client = Client('en-US')
await client.login(
    auth_info_1='username',      # Required: username
    auth_info_2='email@x.com',   # Optional: email (helps bypass some challenges)
    password='your_password'     # Required
)
```

### Cookie Persistence
```python
# Save after login
client.save_cookies('cookies.json')

# Load on subsequent runs (no password needed)
client.load_cookies('cookies.json')
```

### Session Verification
```python
user = await client.user()  # Returns current logged-in user
print(user.screen_name, user.id)
```

## Search API

### `client.search_tweet(query, product, count)`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | str | required | Search query string |
| `product` | str | required | `"Top"`, `"Latest"`, `"People"`, `"Media"` |
| `count` | int | 20 | Number of results per page (max 100) |

Returns a `Result` object (iterable of `Tweet` objects).

### Pagination
```python
results = await client.search_tweet('veo prompt', product='Top', count=100)

# Iterate current page
for tweet in results:
    print(tweet.text)

# Get next page
next_page = await results.next()
if next_page is None:
    print("No more results")
```

## X Advanced Search Operators

Use these in the query string for server-side filtering:

| Operator | Example | Effect |
|----------|---------|--------|
| `min_faves:N` | `min_faves:50` | Likes >= N |
| `min_retweets:N` | `min_retweets:10` | Retweets >= N |
| `min_replies:N` | `min_replies:5` | Replies >= N |
| `since:YYYY-MM-DD` | `since:2024-01-01` | After date |
| `until:YYYY-MM-DD` | `until:2024-12-31` | Before date |
| `from:user` | `from:elonmusk` | From specific user |
| `to:user` | `to:elonmusk` | Replying to user |
| `filter:media` | `filter:media` | Only tweets with media |
| `filter:videos` | `filter:videos` | Only tweets with videos |
| `filter:images` | `filter:images` | Only tweets with images |
| `lang:xx` | `lang:en` | Language filter |
| `-filter:retweets` | `-filter:retweets` | Exclude retweets |

**Combined example:**
```python
query = 'veo prompt min_faves:50 -filter:retweets since:2024-06-01'
```

## Tweet Object Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | str | Tweet ID |
| `text` | str | Full tweet text |
| `created_at` | str | Creation timestamp |
| `favorite_count` | int | Likes count |
| `retweet_count` | int | Retweets count |
| `reply_count` | int | Replies count |
| `quote_count` | int | Quote tweets count |
| `view_count` | int or None | View count (None if unavailable) |
| `bookmark_count` | int or None | Bookmark count |
| `hashtags` | list[str] | Hashtags in the tweet |
| `media` | list[Media] | Attached media objects |
| `author` | User | Tweet author |
| `full_text` | str | Alias for text (truncated tweets) |
| `in_reply_to` | str or None | ID of parent tweet |
| `quoted_tweet` | Tweet or None | Quoted tweet object |
| `retweeted_tweet` | Tweet or None | Original retweeted tweet |
| `place` | Place or None | Geolocation data |
| `source` | str | Client source (e.g., "Twitter Web App") |

## User Object Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | str | User ID |
| `name` | str | Display name |
| `screen_name` | str | Handle (e.g., @username) |
| `description` | str or None | Bio text |
| `followers_count` | int | Follower count |
| `following_count` | int | Following count |
| `statuses_count` | int | Total tweets |
| `favourites_count` | int | Total likes given |
| `listed_count` | int | List memberships |
| `profile_image_url_https` | str | Avatar URL |
| `profile_banner_url` | str or None | Banner URL |
| `is_blue_verified` | bool | Has blue checkmark |
| `created_at` | str | Account creation date |
| `location` | str or None | Profile location |

## Rate Limits

X enforces rate limits per 15-minute window per endpoint:

| Endpoint | Limit | Notes |
|----------|-------|-------|
| Search (Top/Latest/Media) | ~50 requests | Per 15-min window |
| User lookup | ~500 requests | Per 15-min window |
| Tweet detail | ~500 requests | Per 15-min window |

**Mitigation strategies:**
- Use `min_faves:` server-side filter to reduce pages needed
- Set `delay=2.0` (or higher) between pagination requests
- Use `--max-pages` to cap total requests
- If rate-limited, twikit raises `TooManyRequests` — wait 15 min and retry
- Rotate accounts if scraping at scale

## Error Handling

```python
from twikit.errors import TooManyRequests, TwitterException, UserNotFound

try:
    results = await client.search_tweet(query, product='Top')
except TooManyRequests:
    print("Rate limited. Wait 15 minutes.")
except TwitterException as e:
    print(f"Twitter error: {e}")
except UserNotFound:
    print("User not found")
```

## Other Useful Methods

### Get user by username
```python
user = await client.get_user_by_screen_name('elonmusk')
print(user.followers_count)
```

### Get user by ID
```python
user = await client.get_user_by_id('44196397')
```

### Get tweet by ID
```python
tweet = await client.get_tweet('1234567890')
print(tweet.favorite_count, tweet.view_count)
```

### Get user's tweets
```python
tweets = await client.get_user_tweets('44196397', tweet_type='Tweets', count=20)
```

### Get trending topics
```python
trends = await client.get_trends()
```

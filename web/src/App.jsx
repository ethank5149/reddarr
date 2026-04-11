import {useEffect,useState,useRef,useCallback} from "react"
import {NavLink, useLocation, useNavigate} from "react-router-dom"
import axios from "axios"

// PWA Service Worker Registration
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {})
  })
}

// Touch device detection
const isTouchDevice = () => 'ontouchstart' in window || navigator.maxTouchPoints > 0

// Prevent default touch behaviors for gallery
const preventDefault = (e) => { if (e.touches.length > 1) e.preventDefault() }

export default function App(){
  const [token, setToken] = useState(() => localStorage.getItem("token"))
  const [role, setRole] = useState(() => localStorage.getItem("role"))
  const [loginUser, setLoginUser] = useState("")
  const [loginPass, setLoginPass] = useState("")
  const [loginErr, setLoginErr] = useState("")

  useEffect(() => {
    const reqInterceptor = axios.interceptors.request.use(config => {
      const storedToken = localStorage.getItem("token")
      if (storedToken) config.headers.Authorization = `Bearer ${storedToken}`
      return config
    })
    const resInterceptor = axios.interceptors.response.use(r => r, err => {
      if (err.response?.status === 401) {
        setToken(null)
        setRole(null)
        localStorage.removeItem("token")
        localStorage.removeItem("role")
      }
      return Promise.reject(err)
    })
    return () => {
      axios.interceptors.request.eject(reqInterceptor)
      axios.interceptors.response.eject(resInterceptor)
    }
  }, [])

  const location = useLocation()
  const navigate = useNavigate()

  // Parse route — default landing is library
  const pathParts = location.pathname.split('/').filter(Boolean)
  let activeTab = "library"
  let targetDetailType = null
  let targetDetailName = null
  if(pathParts[0] === "library" || pathParts.length === 0) {
    activeTab = "library"
  } else if(pathParts[0] === "subreddits") {
    activeTab = "subreddits"
    if(pathParts[1]) { targetDetailType = "subreddit"; targetDetailName = decodeURIComponent(pathParts[1]) }
  } else if(pathParts[0] === "users") {
    activeTab = "users"
    if(pathParts[1]) { targetDetailType = "user"; targetDetailName = decodeURIComponent(pathParts[1]) }
  } else if(pathParts[0] === "archive") {
    activeTab = "archive"
  } else if(pathParts[0] === "wanted") {
    activeTab = "wanted"
  } else if(pathParts[0] === "system") {
    activeTab = "system"
  } else if(pathParts[0] === "activity") {
    activeTab = "activity"
  } else if(pathParts[0] === "logs") {
    activeTab = "logs"
  }

  // Redirect bare / to /library
  useEffect(() => {
    if(location.pathname === "/") navigate("/library", {replace: true})
  }, [location.pathname])

  // View mode for index pages
  const [viewMode, setViewMode] = useState(() => localStorage.getItem("viewMode") || "grid")
  useEffect(() => { localStorage.setItem("viewMode", viewMode) }, [viewMode])

  // Sidebar collapsed state
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    const saved = localStorage.getItem("sidebarCollapsed")
    return saved === "true"
  })
  useEffect(() => { localStorage.setItem("sidebarCollapsed", String(sidebarCollapsed)) }, [sidebarCollapsed])

  // Target detail posts
  const [targetPosts, setTargetPosts] = useState([])
  const [targetPostsOffset, setTargetPostsOffset] = useState(0)
  const targetPostsOffsetRef = useRef(0)
  const [targetPostsLoading, setTargetPostsLoading] = useState(false)
  const targetPostsLoader = useRef()

  const [posts,setPosts]=useState([])
  const [search,setSearch]=useState("")
  const [searchResults,setSearchResults]=useState(null)
  const [selectedPost,setSelectedPost]=useState(null)
  const [galleryIdx,setGalleryIdx]=useState(0)
  const [archivePosts,setArchivePosts]=useState([])
  const [archiveOffset,setArchiveOffset]=useState(0)
  const archiveOffsetRef=useRef(0)
  const archiveFilteringRef=useRef(false)
  const [archiveFilterSubreddit,setArchiveFilterSubreddit]=useState("")
  const [archiveFilterAuthor,setArchiveFilterAuthor]=useState("")
  const [archiveFilterMediaTypes,setArchiveFilterMediaTypes]=useState([])
  const [archiveSortBy,setArchiveSortBy]=useState("last_added")
  const [archiveShowNsfw,setArchiveShowNsfw]=useState(true)
  const [archiveIsLoading,setArchiveIsLoading]=useState(false)
  const archiveLoader=useRef()
  const archiveFiltersRef=useRef({subreddit:"",author:"",mediaTypes:[],sort:"last_added",nsfw:true})
  const [archiveSearch,setArchiveSearch]=useState("")
  const [archiveSearchResults,setArchiveSearchResults]=useState(null)
  const archiveSearchTimeout=useRef()
  const [adminData,setAdminData]=useState(null)
  const [logs,setLogs]=useState([])
  const [hoveredCard,setHoveredCard]=useState(null)
  const [adminLoading, setAdminLoading] = useState(false)
  const [auditData, setAuditData] = useState(null)
  const [auditPosts, setAuditPosts] = useState([])
  const [auditLoading, setAuditLoading] = useState(false)
  const [auditPostDetail, setAuditPostDetail] = useState(null)
  const [auditFilters, setAuditFilters] = useState({status: "", subreddit: ""})
  const [auditOffset, setAuditOffset] = useState(0)
  const auditOffsetRef = useRef(0)
  const [queueInfo, setQueueInfo] = useState(null)
  const [healthStatus, setHealthStatus] = useState(null)
  const [newPostsAvailable, setNewPostsAvailable] = useState(0)
  const [lastUpdated, setLastUpdated] = useState(null)
  const [liveConnected, setLiveConnected] = useState(false)
  const [resetModal, setResetModal] = useState(false)
  const [resetInput, setResetInput] = useState("")
  const [resetLoading, setResetLoading] = useState(false)
  const [resetResult, setResetResult] = useState(null)
  const [deleteModal, setDeleteModal] = useState(false)
  const [deleteTargetId, setDeleteTargetId] = useState(null)
  const [highlightedRows, setHighlightedRows] = useState(new Set())
  const [addTargetType, setAddTargetType] = useState("subreddit")
  const [addTargetName, setAddTargetName] = useState("")
  const [isTouch, setIsTouch] = useState(false)
  const [swipeStart, setSwipeStart] = useState(null)
  const [headerHeight, setHeaderHeight] = useState(73)
  const [toasts, setToasts] = useState([])
  const [filterBarOpen, setFilterBarOpen] = useState(false)
  const [archiveFilterBarOpen, setArchiveFilterBarOpen] = useState(false)
  const [installPrompt, setInstallPrompt] = useState(null)
  const [showInstallBanner, setShowInstallBanner] = useState(false)

  const [targetIndexSearch, setTargetIndexSearch] = useState("")
  const [targetIndexSortBy, setTargetIndexSortBy] = useState("name_asc")
  const [targetIndexFilterEnabled, setTargetIndexFilterEnabled] = useState(true)
  const [targetIndexFilterStatus, setTargetIndexFilterStatus] = useState("all")

  const [targetDetailSearch, setTargetDetailSearch] = useState("")
  const [targetDetailSortBy, setTargetDetailSortBy] = useState("newest")
  const [targetDetailFilterMediaType, setTargetDetailFilterMediaType] = useState("all")
  const [targetDetailSearchResults, setTargetDetailSearchResults] = useState(null)
  const [targetLiveStats, setTargetLiveStats] = useState(null)
  const [targetFailures, setTargetFailures] = useState([])
  const [targetFailuresLoading, setTargetFailuresLoading] = useState(false)
  const [targetFailuresOpen, setTargetFailuresOpen] = useState(false)

  const targetIndexSearchTimeout = useRef()

  function hasActiveTargetIndexFilters(){
    return targetIndexSearch || targetIndexFilterStatus !== "all"
  }

  function hasActiveTargetDetailFilters(){
    return targetDetailSearch || targetDetailFilterMediaType !== "all"
  }

  function getFilteredAndSortedTargets(items){
    let filtered = [...items]
    if(targetIndexSearch){
      const q = targetIndexSearch.toLowerCase()
      filtered = filtered.filter(t => t.name.toLowerCase().includes(q))
    }
    if(targetIndexFilterStatus !== "all"){
      filtered = filtered.filter(t => {
        if(targetIndexFilterStatus === "enabled") return t.enabled
        if(targetIndexFilterStatus === "disabled") return !t.enabled
        if(targetIndexFilterStatus === "error") return t.status === "error"
        return true
      })
    }
    filtered.sort((a,b) => {
      if(targetIndexSortBy === "name_asc") return a.name.localeCompare(b.name)
      if(targetIndexSortBy === "name_desc") return b.name.localeCompare(a.name)
      if(targetIndexSortBy === "posts_desc") return (b.post_count||0) - (a.post_count||0)
      if(targetIndexSortBy === "posts_asc") return (a.post_count||0) - (b.post_count||0)
      if(targetIndexSortBy === "media_desc") return (b.total_media||0) - (a.total_media||0)
      if(targetIndexSortBy === "media_asc") return (a.total_media||0) - (b.total_media||0)
      return 0
    })
    return filtered
  }

  function getFilteredAndSortedPosts(posts){
    let filtered = [...posts]
    if(targetDetailFilterMediaType !== "all"){
      filtered = filtered.filter(p => {
        if(targetDetailFilterMediaType === "image") return !p.is_video && (p.url || p.image_urls?.length > 0)
        if(targetDetailFilterMediaType === "video") return p.is_video || p.video_url
        if(targetDetailFilterMediaType === "text") return !p.url && !p.image_urls?.length && !p.is_video && p.selftext
        return true
      })
    }
    filtered.sort((a,b) => {
      if(targetDetailSortBy === "newest") return new Date(b.created_utc) - new Date(a.created_utc)
      if(targetDetailSortBy === "oldest") return new Date(a.created_utc) - new Date(b.created_utc)
      if(targetDetailSortBy === "title_asc") return (a.title||"").localeCompare(b.title||"")
      if(targetDetailSortBy === "title_desc") return (b.title||"").localeCompare(a.title||"")
      return 0
    })
    return filtered
  }

  // Detect touch device and handle safe areas
  useEffect(() => {
    const checkTouch = () => setIsTouch(isTouchDevice())
    checkTouch()
    const updateHeaderHeight = () => {
      const viewportHeight = window.visualViewport?.height || window.innerHeight
      const diff = window.innerHeight - viewportHeight
      setHeaderHeight(73 + diff)
    }
    if (window.visualViewport) {
      window.visualViewport.addEventListener('resize', updateHeaderHeight)
    }
    window.addEventListener('resize', updateHeaderHeight)
    return () => {
      if (window.visualViewport) {
        window.visualViewport.removeEventListener('resize', updateHeaderHeight)
      }
      window.removeEventListener('resize', updateHeaderHeight)
    }
  }, [])

  // PWA install prompt
  useEffect(() => {
    const handler = (e) => {
      e.preventDefault()
      setInstallPrompt(e)
      setShowInstallBanner(true)
    }
    window.addEventListener('beforeinstallprompt', handler)
    return () => window.removeEventListener('beforeinstallprompt', handler)
  }, [])

  // Escape key closes modals + keeps keyboard nav working
  useEffect(() => {
    const handler = (e) => {
      if (e.key === 'Escape') {
        if (selectedPost) { setSelectedPost(null); return }
        if (auditPostDetail) { setAuditPostDetail(null); return }
        if (deleteModal) { setDeleteModal(false); return }
        if (resetModal && !resetLoading) { setResetModal(false); return }
      }
      if (e.key === 'ArrowLeft' && selectedPost?.image_urls?.length > 1) {
        setGalleryIdx(i => Math.max(0, i - 1))
      }
      if (e.key === 'ArrowRight' && selectedPost?.image_urls?.length > 1) {
        setGalleryIdx(i => Math.min(selectedPost.image_urls.length - 1, i + 1))
      }
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [selectedPost, auditPostDetail, deleteModal, resetModal, resetLoading])

  // Toast helper
  function showToast(message, type = 'info', duration = 3000) {
    const id = Date.now()
    setToasts(prev => [...prev, { id, message, type }])
    setTimeout(() => setToasts(prev => prev.filter(t => t.id !== id)), duration)
  }

  function toast(msg) { showToast(msg, 'info') }
  function toastSuccess(msg) { showToast(msg, 'success') }
  function toastError(msg) { showToast(msg, 'error') }

  // Thumbnail utility state
  const [thumbStats, setThumbStats] = useState(null)
  const [thumbJob, setThumbJob] = useState(null)
  const [thumbJobResult, setThumbJobResult] = useState(null)
  const thumbPollRef = useRef(null)

  // Bulk archive state
  const [archiveStats, setArchiveStats] = useState(null)
  const [archiveJob, setArchiveJob] = useState(null)
  const [archiveJobResult, setArchiveJobResult] = useState(null)
  const archiveJobPollRef = useRef(null)
  const [archiveBulkFilter, setArchiveBulkFilter] = useState({
    target_type: "", target_name: "", before_days: "", media_status: ""
  })
  const [archivePanelOpen, setArchivePanelOpen] = useState(false)
  const [cardArchiving, setCardArchiving] = useState({})

  // Database backup/restore state
  const [dbStats, setDbStats] = useState(null)
  const [dbBackups, setDbBackups] = useState([])
  const [dbBackupLoading, setDbBackupLoading] = useState(false)
  const [dbBackupResult, setDbBackupResult] = useState(null)
  const [dbRestoreModal, setDbRestoreModal] = useState(false)
  const [dbRestoreLoading, setDbRestoreLoading] = useState(false)
  const [partialRestoreFilters, setPartialRestoreFilters] = useState({subreddits: "", targets: "", before_date: "", after_date: ""})

  // Backfill status
  const [backfillStatus, setBackfillStatus] = useState(null)
  const backfillPollRef = useRef(null)

  // Scrape trigger feedback
  const [scrapeTriggered, setScrapeTriggered] = useState(false)
  const [backfillTriggered, setBackfillTriggered] = useState(false)

  // Per-target card state
  const [expandedCard, setExpandedCard] = useState(null)
  const [cardAudit, setCardAudit] = useState({})
  const [cardAuditLoading, setCardAuditLoading] = useState({})
  const [cardScraping, setCardScraping] = useState({})
  const [cardBackfilling, setCardBackfilling] = useState({})

  // Admin section collapse state
  const [adminSections, setAdminSections] = useState({
    status: true, overview: true, archive: true, targets: true, thumbnails: true, media: true, database: true, activity: true
  })

  // Filter + sort state
  const [filterSubreddit, setFilterSubreddit] = useState("")
  const [filterAuthor, setFilterAuthor] = useState("")
  const [filterMediaTypes, setFilterMediaTypes] = useState([])
  const [showNsfw, setShowNsfw] = useState(() => {
    const saved = localStorage.getItem("showNsfw")
    return saved !== null ? saved === "true" : true
  })
  const [sortBy, setSortBy] = useState("last_added")
  const [isLoading, setIsLoading] = useState(false)

  const offsetRef = useRef(0)
  const filtersRef = useRef({
    subreddit:"", author:"", mediaTypes:[], sort:"last_added",
    nsfw: localStorage.getItem("showNsfw") !== null ? localStorage.getItem("showNsfw") === "true" : true
  })
  const filteringRef = useRef(false)

  const loader=useRef()
  const searchTimeout=useRef()
  const targetDetailSearchTimeout=useRef()
  const targetDetailSearchResultsRef=useRef(null)
  const esRef=useRef(null)
  const highlightTimerRef=useRef(null)

  // SSE real-time connection
  useEffect(()=>{
    function connect(){
      if(esRef.current) esRef.current.close()
      const es = new EventSource("/api/events")
      esRef.current = es
      es.onopen = () => setLiveConnected(true)
      es.onmessage = (e) => {
        console.log("SSE message received:", e.data)
        try {
          const data = JSON.parse(e.data)
          if(data.error) return
          setLastUpdated(new Date())
          setAdminData(prev => ({
            ...(prev || {}),
            total_posts: data.total_posts ?? prev?.total_posts,
            total_comments: data.total_comments ?? prev?.total_comments,
            downloaded_media: data.downloaded_media ?? prev?.downloaded_media,
            pending_media: data.pending_media ?? prev?.pending_media,
            total_media: data.total_media ?? prev?.total_media,
            targets: data.targets ?? prev?.targets,
          }))
          if(data.health) setHealthStatus(data.health)
          setQueueInfo(prev => ({
            ...(prev||{}),
            queue_length: data.queue_length ?? (prev?.queue_length ?? 0)
          }))
          if(data.new_posts && data.new_posts.length > 0){
            const newIds = new Set(data.new_posts.map(p => p.id))
            setHighlightedRows(newIds)
            if(highlightTimerRef.current) clearTimeout(highlightTimerRef.current)
            highlightTimerRef.current = setTimeout(() => setHighlightedRows(new Set()), 4000)
            setLogs(prev => [
              ...data.new_posts.map(p=>({
                id: p.id, subreddit: p.subreddit, author: p.author, created_utc: p.created_utc, title: p.title
              })),
              ...prev
            ].slice(0,50))
            refreshPosts()
          }
          if(data.new_media && data.new_media.length > 0){
            if(filtersRef.current.sort === "last_added"){
            console.log("SSE: calling refreshPosts for", data.new_posts.length, "new posts")
            refreshPosts()
            }
          }
        } catch(err){
          console.error("SSE parse error:", err)
        }
      }
      es.onerror = () => {
        setLiveConnected(false)
        es.close()
        esRef.current = null
        setTimeout(connect, 5000)
      }
    }
    connect()
    return () => { if(esRef.current) esRef.current.close() }
  },[])

  useEffect(()=>{
    // Load targets on startup so sidebar counts populate
    loadAdmin()
  },[])

  useEffect(()=>{
    if(activeTab === "system"){
      if(!adminData) loadAdmin()
      loadThumbStats()
      loadDbStats()
      loadDbBackups()
    }
    if(activeTab === "wanted"){
      loadAuditSummary()
      loadAuditPosts()
    }
    if(activeTab === "library" && posts.length === 0 && offsetRef.current === 0){
      load()
    }
    if((activeTab === "subreddits" || activeTab === "users") && !adminData?.targets){
      loadAdmin()
    }
  },[activeTab])

  // Load target detail posts
  useEffect(()=>{
    if(targetDetailType && targetDetailName){
      setTargetPosts([])
      targetPostsOffsetRef.current = 0
      setTargetPostsLoading(true)
      loadTargetPosts(targetDetailType, targetDetailName, 0)
    }
  },[targetDetailType, targetDetailName])

  function loadTargetPosts(ttype, name, offset){
    const filterKey = ttype === "subreddit" ? "subreddit" : "author"
    const params = new URLSearchParams({limit:"50", offset:String(offset), _t: Date.now().toString()})
    params.set(filterKey, name)
    params.set("sort_by","ingested_at")
    params.set("sort_order","desc")
    console.log("loadTargetPosts fetching:", params.toString())
    axios.get(`/api/posts?${params.toString()}`)
      .then(r=>{
        const newPosts = r.data.posts?.map(mapPost) || []
        console.log("loadTargetPosts got", newPosts.length, "posts")
        if(offset === 0) {
          setTargetPosts(newPosts)
        } else {
          setTargetPosts(prev=>[...prev,...newPosts])
        }
        targetPostsOffsetRef.current = offset + 50
        setTargetPostsLoading(false)
      })
      .catch(()=>setTargetPostsLoading(false))
  }

  function loadTargetStats(ttype, name){
    axios.get(`/api/admin/target/${ttype}/${encodeURIComponent(name)}/stats`)
      .then(r=>setTargetLiveStats(r.data))
      .catch(()=>setTargetLiveStats(null))
  }

  function loadTargetFailures(ttype, name){
    setTargetFailuresLoading(true)
    axios.get(`/api/admin/target/${ttype}/${encodeURIComponent(name)}/failures?limit=20`)
      .then(r=>setTargetFailures(r.data.failures||[]))
      .catch(()=>setTargetFailures([]))
      .finally(()=>setTargetFailuresLoading(false))
  }

  // Target detail infinite scroll
  useEffect(()=>{
    if(!targetDetailType || !targetDetailName) return
    const obs = new IntersectionObserver(entries=>{
      if(entries[0].isIntersecting && !targetPostsLoading) {
        loadTargetPosts(targetDetailType, targetDetailName, targetPostsOffsetRef.current)
      }
    })
    if(targetPostsLoader.current) obs.observe(targetPostsLoader.current)
    return ()=> obs.disconnect()
  },[targetPostsLoader.current, targetDetailType, targetDetailName, targetPostsLoading])

  // Poll for new target posts every 10s
  useEffect(()=>{
    if(!targetDetailType || !targetDetailName) return
    console.log("Starting target posts poll for", targetDetailType, targetDetailName)
    const poll = setInterval(()=>{
      console.log("Polling target posts for", targetDetailName)
      loadTargetPosts(targetDetailType, targetDetailName, 0)
    }, 10000)
    return ()=> clearInterval(poll)
  },[targetDetailType, targetDetailName])

  // Poll for target live stats every 15s
  useEffect(()=>{
    if(!targetDetailType || !targetDetailName) return
    loadTargetStats(targetDetailType, targetDetailName)
    loadTargetFailures(targetDetailType, targetDetailName)
    const poll = setInterval(()=>{
      loadTargetStats(targetDetailType, targetDetailName)
      if(targetFailuresOpen) loadTargetFailures(targetDetailType, targetDetailName)
    }, 15000)
    return ()=> clearInterval(poll)
  },[targetDetailType, targetDetailName, targetFailuresOpen])

  // Polling fallback every 10s on system and activity tabs
  useEffect(()=>{
    if(activeTab !== "system" && activeTab !== "activity") return
    const poll = setInterval(()=>{
      axios.get("/api/admin/stats").then(r=>{ if(r.data) setAdminData(r.data) }).catch(()=>{})
      axios.get("/api/admin/queue").then(r=>{ if(r.data) setQueueInfo(r.data) }).catch(()=>{})
      axios.get("/api/admin/health").then(r=>{ if(r.data) setHealthStatus(r.data) }).catch(()=>{})
      axios.get("/api/admin/activity?limit=50&include_failures=true").then(r=>{ if(r.data) setLogs(r.data) }).catch(()=>{})
      setLastUpdated(new Date())
    }, 10000)
    return ()=> clearInterval(poll)
  },[activeTab])

  // Fetch full post detail when modal opens
  useEffect(()=>{
    if(!selectedPost?.id) return
    axios.get(`/api/post/${selectedPost.id}`)
      .then(r=>{
        if(!r.data) return
        setSelectedPost(prev => prev?.id === r.data.id ? {
          ...prev,
          comments: r.data.comments || [],
          created_utc: r.data.created_utc || prev?.created_utc,
          video_url: r.data.video_url ?? prev?.video_url,
          is_video: r.data.is_video ?? prev?.is_video,
          url: r.data.image_url ?? prev?.url,
          hidden: r.data.hidden ?? prev?.hidden,
          image_urls: r.data.image_urls ?? prev?.image_urls,
          video_urls: r.data.video_urls ?? prev?.video_urls,
        } : prev)
      })
      .catch(()=>{})
  },[selectedPost?.id])

  function buildPostsQuery(offset, filtersOverride, hiddenFlag=false){
    const f = filtersOverride || filtersRef.current
    const params = new URLSearchParams({ limit:"50", offset:String(offset), _t: Date.now().toString() })
    if(hiddenFlag) params.set("hidden","true")
    if(f.subreddit) params.set("subreddit", f.subreddit)
    if(f.author) params.set("author", f.author)
    if(f.mediaTypes && f.mediaTypes.length > 0){
      f.mediaTypes.forEach(mt => params.append("media_type", mt))
    }
    if(f.sort === "oldest"){ params.set("sort_by","created_utc"); params.set("sort_order","asc") }
    else if(f.sort === "title_asc"){ params.set("sort_by","title"); params.set("sort_order","asc") }
    else if(f.sort === "title_desc"){ params.set("sort_by","title"); params.set("sort_order","desc") }
    else if(f.sort === "last_added"){ params.set("sort_by","ingested_at"); params.set("sort_order","desc") }
    else { params.set("sort_by","created_utc"); params.set("sort_order","desc") }
    if(f.nsfw === false) params.set("nsfw", "exclude")
    return `/api/posts?${params.toString()}`
  }

  function mapPost(p){
    return {
      id:p.id, title:p.title,
      url:p.image_url, image_urls:p.image_urls,
      video_url:p.video_url, video_urls:p.video_urls,
      is_video:p.is_video, selftext:p.selftext,
      subreddit:p.subreddit, author:p.author,
      created_utc:p.created_utc, thumb_url:p.thumb_url, preview_url:p.preview_url,
      hidden:p.hidden
    }
  }

  function load(){
    if(filteringRef.current) return
    const currentOffset = offsetRef.current
    axios.get(buildPostsQuery(currentOffset))
    .then(r=>{
      if(filteringRef.current) return
      const newPosts = r.data.posts?.map(mapPost) || []
      setPosts(prev=>[...prev,...newPosts])
      offsetRef.current = currentOffset + 50
    }).catch(err=>console.error("Failed to load posts:", err))
  }

  function refreshPosts(){
    offsetRef.current = 0
    axios.get(buildPostsQuery(0))
    .then(r=>{
      const newPosts = r.data.posts?.map(mapPost) || []
      setPosts(newPosts)
      offsetRef.current = 50
      setNewPostsAvailable(0)
    }).catch(err=>console.error("Failed to refresh posts:", err))
  }

  function applyFilters(newFilters){
    filtersRef.current = newFilters
    offsetRef.current = 0
    filteringRef.current = true
    setIsLoading(true)
    setPosts([])
    axios.get(buildPostsQuery(0))
    .then(r=>{
      setPosts(r.data.posts?.map(mapPost) || [])
      offsetRef.current = 50
    }).catch(err=>console.error("Failed to load posts:", err.response?.data || err.message || err))
    .finally(()=>{ filteringRef.current = false; setIsLoading(false) })
  }

  function hasActiveFilters(){
    const f = filtersRef.current
    return f.subreddit || f.author || (f.mediaTypes && f.mediaTypes.length > 0) || f.sort !== "last_added" || f.nsfw !== true
  }

  function hasActiveArchiveFilters(){
    const f = archiveFiltersRef.current
    return f.subreddit || f.author || (f.mediaTypes && f.mediaTypes.length > 0) || f.sort !== "last_added" || f.nsfw !== true
  }

  function clearFilters(){
    const defaultFilters = { subreddit:"", author:"", mediaTypes:[], sort:"last_added", nsfw:true }
    setFilterSubreddit(""); setFilterAuthor(""); setFilterMediaTypes([]); setSortBy("last_added")
    applyFilters(defaultFilters)
  }

  function loadArchive(){
    if(archiveFilteringRef.current) return
    const currentOffset = archiveOffsetRef.current
    axios.get(buildPostsQuery(currentOffset, archiveFiltersRef.current, true))
    .then(r=>{
      if(archiveFilteringRef.current) return
      const newPosts = r.data.map(mapPost)
      setArchivePosts(prev=>[...prev,...newPosts])
      archiveOffsetRef.current = currentOffset + 50
    }).catch(err=>console.error("Failed to load archive posts:", err))
  }

  function applyArchiveFilters(newFilters){
    archiveFiltersRef.current = newFilters
    archiveOffsetRef.current = 0
    archiveFilteringRef.current = true
    setArchiveIsLoading(true)
    setArchivePosts([])
    axios.get(buildPostsQuery(0, newFilters, true))
    .then(r=>{
      setArchivePosts(r.data.map(mapPost))
      archiveOffsetRef.current = 50
    }).catch(err=>console.error("Failed to load archive posts:", err))
    .finally(()=>{ archiveFilteringRef.current=false; setArchiveIsLoading(false) })
  }

  function clearArchiveFilters(){
    const d={subreddit:"",author:"",mediaTypes:[],sort:"last_added",nsfw:true}
    setArchiveFilterSubreddit(""); setArchiveFilterAuthor(""); setArchiveFilterMediaTypes([]); setArchiveSortBy("last_added")
    applyArchiveFilters(d)
  }

  function hidePost(postId){
    axios.post(`/api/post/${postId}/hide`)
      .then(()=>{
        setPosts(prev=>prev.filter(p=>p.id!==postId))
        setTargetPosts(prev=>prev.filter(p=>p.id!==postId))
        setArchivePosts([]); archiveOffsetRef.current=0
        if(selectedPost?.id===postId) setSelectedPost(prev=>({...prev,hidden:true}))
        toastSuccess("Post hidden")
      })
      .catch(()=>toastError("Failed to hide post"))
  }

  function unhidePost(postId){
    axios.post(`/api/post/${postId}/unhide`)
      .then(()=>{
        setArchivePosts(prev=>prev.filter(p=>p.id!==postId))
        setPosts([]); offsetRef.current=0
        if(selectedPost?.id===postId) setSelectedPost(prev=>({...prev,hidden:false}))
        toastSuccess("Post unhidden")
      })
      .catch(()=>toastError("Failed to unhide post"))
  }

  function deletePost(postId){
    setDeleteTargetId(postId)
    setDeleteModal(true)
  }

  function confirmDeletePost(){
    if(!deleteTargetId) return
    axios.delete(`/api/post/${deleteTargetId}`)
      .then(()=>{
        setPosts(prev=>prev.filter(p=>p.id!==deleteTargetId))
        setArchivePosts(prev=>prev.filter(p=>p.id!==deleteTargetId))
        setTargetPosts(prev=>prev.filter(p=>p.id!==deleteTargetId))
        if(selectedPost?.id===deleteTargetId) setSelectedPost(null)
        toastSuccess("Post deleted")
      })
      .catch(()=>toastError("Failed to delete post"))
      .finally(()=>{ setDeleteModal(false); setDeleteTargetId(null) })
  }

  function handleArchiveSearch(e){
    setArchiveSearch(e.target.value)
    clearTimeout(archiveSearchTimeout.current)
    if(!e.target.value.trim()){ setArchiveSearchResults(null); return }
    archiveSearchTimeout.current=setTimeout(()=>{
      axios.get(`/api/search?q=${encodeURIComponent(e.target.value)}&hidden=true`)
        .then(r=>setArchiveSearchResults(r.data.map(p=>({id:p.id,title:p.title,subreddit:p.subreddit,author:p.author,created_utc:p.created_utc}))))
    },300)
  }

  function loadAdmin(){
    setAdminLoading(true)
    Promise.all([
      axios.get("/api/admin/stats").catch(()=>({data:null})),
      axios.get("/api/admin/activity?limit=50&include_failures=true").catch(()=>({data:[]})),
      axios.get("/api/admin/queue").catch(()=>({data:null})),
      axios.get("/api/admin/health").catch(()=>({data:null}))
    ]).then(([statsRes,logsRes,queueRes,healthRes])=>{
      if(statsRes.data) setAdminData(statsRes.data)
      if(logsRes.data) setLogs(logsRes.data)
      if(queueRes.data) setQueueInfo(queueRes.data)
      if(healthRes.data) setHealthStatus(healthRes.data)
      setAdminLoading(false)
    }).catch(()=>setAdminLoading(false))
  }

  function toggleTarget(ttype,name){
    axios.post(`/api/admin/target/${ttype}/${encodeURIComponent(name)}/toggle`).then(()=>loadAdmin()).catch(()=>toastError("Failed to toggle target"))
  }

  function setTargetStatus(ttype,name,status){
    axios.post(`/api/admin/target/${ttype}/${encodeURIComponent(name)}/status?new_status=${status}`).then(()=>loadAdmin()).catch(()=>toastError("Failed to set status"))
  }

  function rescanTarget(ttype,name){
    axios.post(`/api/admin/target/${ttype}/${encodeURIComponent(name)}/rescan`).then(()=>{ toastSuccess("Rescan queued"); loadAdmin() }).catch(()=>toastError("Failed to rescan target"))
  }

  function scrapeNow(){
    axios.post("/api/admin/scrape")
      .then(()=>{ setScrapeTriggered(true); setTimeout(()=>setScrapeTriggered(false), 3000) })
      .catch(()=>toastError("Failed to trigger scrape"))
  }

  function triggerBackfill(){
    axios.post(`/api/admin/backfill?passes=2&workers=3`)
      .then(()=>{
        setBackfillTriggered(true)
        startBackfillPoll()
        setTimeout(()=>setBackfillTriggered(false), 3000)
      })
      .catch(()=>toastError("Failed to trigger backfill"))
  }

  function startBackfillPoll(){
    if(backfillPollRef.current) clearInterval(backfillPollRef.current)
    backfillPollRef.current = setInterval(()=>{
      axios.get("/api/admin/backfill/status")
        .then(r=>{
          setBackfillStatus(r.data)
          if(r.data.status === "done" || r.data.status === "partial" || r.data.status === "none"){
            clearInterval(backfillPollRef.current)
            backfillPollRef.current = null
          }
        })
        .catch(()=>setBackfillStatus(null))
    }, 2000)
  }

  useEffect(()=>{
    return () => { if(backfillPollRef.current) clearInterval(backfillPollRef.current) }
  }, [])

  function deleteTarget(ttype,name){
    const removeOnly = window.confirm(`Delete target ${ttype}:${name}?\n\nClick OK to remove from scrape list only (keeps posts and media).\nClick Cancel to abort.`)
    if (!removeOnly) return
    const shouldPrune = window.confirm(`Also delete all posts and media associated with ${name}?\n\nClick OK to delete posts and media.\nClick Cancel to keep them.`)
    if (!shouldPrune) {
      axios.delete(`/api/admin/target/${ttype}/${encodeURIComponent(name)}`).then(()=>{ toastSuccess("Target removed"); loadAdmin() }).catch(()=>toastError("Failed to delete target"))
    } else {
      const alsoDeleteFiles = window.confirm("Also delete downloaded media files from disk? (This cannot be undone)")
      axios.delete(`/api/admin/target/${ttype}/${encodeURIComponent(name)}?prune=true&delete_files=${alsoDeleteFiles}`)
        .then(r=>{ toastSuccess(`Deleted: ${r.data.deleted_posts} posts, ${r.data.deleted_media} media, ${r.data.deleted_files} files`); loadAdmin() })
        .catch(()=>toastError("Failed to delete target"))
    }
  }

  function addTarget(){
    const name = addTargetName.trim()
    if(!name) return
    axios.post(`/api/admin/target/${addTargetType}?name=${encodeURIComponent(name)}`)
      .then(()=>{ setAddTargetName(""); toastSuccess(`Added ${addTargetType}: ${name}`); loadAdmin() })
      .catch(()=>toastError("Failed to add target"))
  }

  function toggleCardExpand(ttype, name){
    const key = `${ttype}:${name}`
    if(expandedCard === key){
      setExpandedCard(null)
    } else {
      setExpandedCard(key)
      if(!cardAudit[key] && !cardAuditLoading[key]){
        fetchCardAudit(ttype, name)
      }
    }
  }

  function toggleAdminSection(section){
    setAdminSections(prev => ({...prev, [section]: !prev[section]}))
  }
  function collapseAllSections(){
    setAdminSections({ status:false, overview:false, archive:false, targets:false, thumbnails:false, media:false, database:false, activity:false })
  }
  function expandAllSections(){
    setAdminSections({ status:true, overview:true, archive:true, targets:true, thumbnails:true, media:true, database:true, activity:true })
  }

  function fetchCardAudit(ttype, name){
    const key = `${ttype}:${name}`
    setCardAuditLoading(prev => ({...prev, [key]: true}))
    axios.get(`/api/admin/target/${ttype}/${encodeURIComponent(name)}/audit`)
      .then(r => {
        setCardAudit(prev => ({...prev, [key]: r.data}))
        setCardAuditLoading(prev => ({...prev, [key]: false}))
      })
      .catch(() => {
        setCardAuditLoading(prev => ({...prev, [key]: false}))
        toastError(`Audit failed for ${ttype}:${name}`)
      })
  }

  function scrapeTargetNow(ttype, name){
    const key = `${ttype}:${name}`
    setCardScraping(prev => ({...prev, [key]: true}))
    axios.post(`/api/admin/target/${ttype}/${encodeURIComponent(name)}/scrape`)
      .then(() => {
        toastSuccess(`Scrape triggered for ${ttype === "subreddit" ? "r/" : "u/"}${name}`)
        setTimeout(() => setCardScraping(prev => ({...prev, [key]: false})), 3000)
      })
      .catch(() => {
        toastError(`Failed to trigger scrape for ${name}`)
        setCardScraping(prev => ({...prev, [key]: false}))
      })
  }

  function rescrapeTargetNow(ttype, name){
    const key = `${ttype}:${name}`
    setCardScraping(prev => ({...prev, [key]: true}))
    axios.post(`/api/admin/target/${ttype}/${encodeURIComponent(name)}/rescrape`)
      .then(r => {
        toastSuccess(`Requeued ${r.data.requeued} missing items for ${name}`)
        setTimeout(() => setCardScraping(prev => ({...prev, [key]: false})), 3000)
      })
      .catch(() => {
        toastError(`Failed to rescrape missing items for ${name}`)
        setCardScraping(prev => ({...prev, [key]: false}))
      })
  }

  function backfillTargetNow(ttype, name){
    const key = `${ttype}:${name}`
    setCardBackfilling(prev => ({...prev, [key]: true}))
    axios.post(`/api/admin/target/${ttype}/${encodeURIComponent(name)}/backfill?passes=2&workers=3`)
      .then(() => {
        toastSuccess(`Backfill triggered for ${ttype === "subreddit" ? "r/" : "u/"}${name}`)
        startBackfillPoll()
        setTimeout(() => setCardBackfilling(prev => ({...prev, [key]: false})), 3000)
      })
      .catch(() => {
        toastError(`Failed to trigger backfill for ${name}`)
        setCardBackfilling(prev => ({...prev, [key]: false}))
      })
  }

  function clearQueue(){
    if(!window.confirm("Clear the entire download queue?")) return
    axios.delete("/api/admin/queue").then(()=>{ toastSuccess("Queue cleared"); loadAdmin() }).catch(()=>toastError("Failed to clear queue"))
  }

  function doReset(){
    if(resetInput !== "RESET") return
    setResetLoading(true)
    axios.delete("/api/admin/reset?confirm=RESET")
      .then(r=>{
        setResetResult(r.data)
        setResetLoading(false)
        setPosts([]); offsetRef.current=0; setNewPostsAvailable(0); setLogs([])
        setArchivePosts([]); archiveOffsetRef.current=0
        loadAdmin()
      })
      .catch(err=>{
        setResetResult({error: err.response?.data?.detail || err.message})
        setResetLoading(false)
      })
  }

  function loadThumbStats(){
    axios.get("/api/admin/thumbnails/stats").then(r=>setThumbStats(r.data)).catch(()=>setThumbStats(null))
  }

  function startThumbPoll(jobId){
    if(thumbPollRef.current) clearInterval(thumbPollRef.current)
    thumbPollRef.current = setInterval(()=>{
      axios.get(`/api/admin/thumbnails/job/${jobId}`)
        .then(r=>{
          setThumbJob(r.data)
          if(r.data.status==="done"){
            clearInterval(thumbPollRef.current); thumbPollRef.current = null
            setThumbJobResult(r.data); setThumbJob(null); loadThumbStats()
          }
        })
        .catch(()=>{ clearInterval(thumbPollRef.current); thumbPollRef.current = null })
    }, 1000)
  }

  function runThumbBackfill(){
    setThumbJobResult(null)
    axios.post("/api/admin/thumbnails/backfill")
      .then(r=>{ setThumbJob({status:"pending", total:r.data.total, done:0, skipped:0, errors:[]}); startThumbPoll(r.data.job_id) })
      .catch(err=>toastError("Backfill failed: " + (err.response?.data?.detail||err.message)))
  }

  function runThumbRebuildAll(){
    if(!window.confirm("Regenerate ALL thumbnails?")) return
    setThumbJobResult(null)
    axios.post("/api/admin/thumbnails/rebuild-all")
      .then(r=>{ setThumbJob({status:"pending", total:r.data.total, done:0, skipped:0, errors:[]}); startThumbPoll(r.data.job_id) })
      .catch(err=>toastError("Rebuild failed: " + (err.response?.data?.detail||err.message)))
  }

  function runThumbPurgeOrphans(){
    if(!window.confirm("Delete all orphan thumbnail files?")) return
    axios.post("/api/admin/thumbnails/purge-orphans")
      .then(r=>{ toastSuccess(`Deleted ${r.data.deleted} orphan file(s), freed ${r.data.freed_mb} MB`); loadThumbStats() })
      .catch(err=>toastError("Purge failed: " + (err.response?.data?.detail||err.message)))
  }

  function loadArchiveStats(){
    axios.get("/api/admin/archive/stats").then(r=>setArchiveStats(r.data)).catch(()=>setArchiveStats(null))
  }

  function loadDbStats(){
    axios.get("/api/admin/db/stats").then(r=>setDbStats(r.data)).catch(()=>setDbStats(null))
  }

  function loadDbBackups(){
    axios.get("/api/admin/db/backups").then(r=>setDbBackups(r.data||[])).catch(()=>setDbBackups([]))
  }

  function createDbBackup(label){
    setDbBackupLoading(true)
    setDbBackupResult(null)
    axios.post(`/api/admin/db/backup?label=${encodeURIComponent(label||"")}`)
      .then(r=>{
        setDbBackupResult(r.data)
        loadDbBackups()
        toastSuccess(`Backup created: ${r.data.label}`)
      })
      .catch(err=>toastError("Backup failed: " + (err.response?.data?.detail||err.message)))
      .finally(()=>setDbBackupLoading(false))
  }

  function createPartialBackup(label, filters, tables){
    setDbBackupLoading(true)
    setDbBackupResult(null)
    const params = new URLSearchParams()
    params.set("label", label || "partial")
    if(filters.subreddits) params.set("subreddits", filters.subreddits)
    if(filters.targets) params.set("targets", filters.targets)
    if(filters.before_date) params.set("before_date", filters.before_date)
    if(filters.after_date) params.set("after_date", filters.after_date)
    if(tables) params.set("tables", tables)
    axios.post(`/api/admin/db/backup/partial?${params.toString()}`)
      .then(r=>{
        setDbBackupResult(r.data)
        loadDbBackups()
        toastSuccess(`Partial backup: ${r.data.filters?.tables?.join(",")}`)
      })
      .catch(err=>toastError("Partial backup failed: " + (err.response?.data?.detail||err.message)))
      .finally(()=>setDbBackupLoading(false))
  }

  function deleteBackup(name){
    if(!window.confirm(`Delete ${name}? This cannot be undone.`)) return
    axios.delete(`/api/admin/db/backup/${encodeURIComponent(name)}`)
      .then(r=>{
        loadDbBackups()
        toastSuccess("Backup deleted")
      })
      .catch(err=>toastError("Delete failed: " + (err.response?.data?.detail||err.message)))
  }

  function getBackupInfo(name){
    axios.get(`/api/admin/db/backup/${encodeURIComponent(name)}/info`)
      .then(r=>setDbBackupResult(r.data))
      .catch(err=>toastError("Info failed: " + (err.response?.data?.detail||err.message)))
  }

  const [mergeBackupsState, setMergeBackupsState] = useState({sources: "", output: "", result: null, loading: false})

  function runMergeBackups(mode){
    const m = mergeBackupsState
    if(!m.sources || !m.output){
      toastError("Select source backups and output name")
      return
    }
    setMergeBackupsState(prev=>({...prev, loading: true}))
    const params = new URLSearchParams()
    params.set("sources", m.sources)
    params.set("output", m.output)
    if(mode === "restore") params.set("confirm", "MERGE")
    axios.post(`/api/admin/db/backup/merge?${params.toString()}`)
      .then(r=>{
        setMergeBackupsState(prev=>({...prev, result: r.data}))
        if(r.data.status === "ok"){
          loadDbBackups()
          toastSuccess(`Merged: ${r.data.counts?.posts} posts`)
        } else if(r.data.status === "preview"){
          toastSuccess(`Would merge: ${r.data.would_merge?.posts} posts`)
        }
      })
      .catch(err=>toastError("Merge failed: " + (err.response?.data?.detail||err.message)))
      .finally(()=>setMergeBackupsState(prev=>({...prev, loading: false})))
  }

  function restoreDbBackup(name){
    if(!window.confirm(`Restoring from ${name} will replace all current data. Continue?`)) return
    setDbRestoreLoading(true)
    axios.post(`/api/admin/db/restore?name=${encodeURIComponent(name)}&confirm=RESTORE`)
      .then(r=>{
        toastSuccess("Database restored successfully")
        loadDbStats()
      })
      .catch(err=>toastError("Restore failed: " + (err.response?.data?.detail||err.message)))
      .finally(()=>setDbRestoreLoading(false))
  }

  const [partialRestoreBackup, setPartialRestoreBackup] = useState("")
  const [partialRestoreResult, setPartialRestoreResult] = useState(null)
  const [partialRestoreLoading, setPartialRestoreLoading] = useState(false)

  function runPartialRestore(mode){
    const f = partialRestoreFilters
    const backup = partialRestoreBackup || dbBackups[0]?.name
    if(!backup){
      toastError("Select a backup file first")
      return
    }
    if(!f.subreddits && !f.targets && !f.before_date && !f.after_date){
      toastError("At least one filter required")
      return
    }
    const params = new URLSearchParams()
    params.set("name", backup)
    if(f.subreddits) params.set("subreddits", f.subreddits)
    if(f.targets) params.set("targets", f.targets)
    if(f.before_date) params.set("before_date", f.before_date)
    if(f.after_date) params.set("after_date", f.after_date)
    if(mode === "restore") params.set("confirm", "RESTORE")
    
    setPartialRestoreLoading(true)
    axios.post(`/api/admin/db/partial-restore?${params.toString()}`)
      .then(r=>{
        setPartialRestoreResult(r.data)
        if(r.data.status === "ok"){
          toastSuccess(`Restored ${r.data.restored?.posts || 0} posts`)
          loadDbStats()
        } else if(r.data.status === "preview"){
          toastSuccess(`Would restore: ${r.data.would_restore?.posts || 0} posts`)
        }
      })
      .catch(err=>toastError("Partial restore failed: " + (err.response?.data?.detail||err.message)))
      .finally(()=>setPartialRestoreLoading(false))
  }

  function startArchiveJobPoll(jobId){
    if(archiveJobPollRef.current) clearInterval(archiveJobPollRef.current)
    archiveJobPollRef.current = setInterval(()=>{
      axios.get(`/api/admin/archive/job/${jobId}`)
        .then(r=>{
          setArchiveJob(r.data)
          if(r.data.status === "done"){
            clearInterval(archiveJobPollRef.current); archiveJobPollRef.current = null
            setArchiveJobResult(r.data); setArchiveJob(null); loadArchiveStats(); loadAdmin()
          }
        })
        .catch(()=>{ clearInterval(archiveJobPollRef.current); archiveJobPollRef.current = null })
    }, 1500)
  }

  function runArchiveAll(){
    if(!window.confirm(`Archive ALL unhidden posts?`)) return
    setArchiveJobResult(null)
    axios.post("/api/admin/archive/all")
      .then(r=>{
        if(!r.data.job_id){ toastSuccess(r.data.message || "Nothing to archive"); return }
        setArchiveJob({status:"pending", total:r.data.total, done:0, skipped:0, files_moved:0, errors:[]})
        startArchiveJobPoll(r.data.job_id)
        toastSuccess(`Archiving ${r.data.total.toLocaleString()} posts…`)
      })
      .catch(err=>toastError("Archive all failed: " + (err.response?.data?.detail||err.message)))
  }

  function runBulkArchiveFiltered(){
    const f = archiveBulkFilter
    const params = new URLSearchParams()
    if(f.target_type && f.target_name){ params.set("target_type", f.target_type); params.set("target_name", f.target_name) }
    if(f.before_days) params.set("before_days", f.before_days)
    if(f.media_status) params.set("media_status", f.media_status)
    axios.post(`/api/admin/archive/bulk?${params.toString()}&dry_run=true`)
      .then(r=>{
        if(r.data.post_count === 0){ toastSuccess("No posts match these filters"); return }
        if(!window.confirm(`Archive ${r.data.post_count.toLocaleString()} post(s)?`)) return
        setArchiveJobResult(null)
        return axios.post(`/api/admin/archive/bulk?${params.toString()}`)
          .then(res=>{
            if(!res.data.job_id){ toastSuccess(res.data.message || "Nothing to archive"); return }
            setArchiveJob({status:"pending", total:res.data.total, done:0, skipped:0, files_moved:0, errors:[]})
            startArchiveJobPoll(res.data.job_id)
            toastSuccess(`Archiving ${res.data.total.toLocaleString()} posts…`)
          })
      })
      .catch(err=>toastError("Bulk archive failed: " + (err.response?.data?.detail||err.message)))
  }

  function runArchiveTarget(ttype, name){
    const key = `${ttype}:${name}`
    setCardArchiving(prev=>({...prev, [key]:true}))
    axios.post(`/api/admin/target/${ttype}/${encodeURIComponent(name)}/archive-all`)
      .then(r=>{
        if(!r.data.job_id){ toastSuccess(r.data.message || "Nothing to archive"); setCardArchiving(prev=>({...prev, [key]:false})); return }
        setArchiveJob({status:"pending", total:r.data.total, done:0, skipped:0, files_moved:0, errors:[]})
        startArchiveJobPoll(r.data.job_id)
        toastSuccess(`Archiving ${r.data.total.toLocaleString()} posts from ${ttype==="subreddit"?"r/":"u/"}${name}`)
        setCardArchiving(prev=>({...prev, [key]:false}))
      })
      .catch(err=>{
        toastError(`Archive failed: ` + (err.response?.data?.detail||err.message))
        setCardArchiving(prev=>({...prev, [key]:false}))
      })
  }

  useEffect(()=>{ return ()=>{ if(archiveJobPollRef.current) clearInterval(archiveJobPollRef.current) } }, [])
  useEffect(()=>{
    if(activeTab === "system" && !archiveStats) loadArchiveStats()
  }, [activeTab])

  function formatRate(rate){
    if(!rate) return "—"
    const perDay = rate * 86400
    if(perDay < 1) return `${(perDay*7).toFixed(1)}/wk`
    return `${perDay.toFixed(1)}/day`
  }

  function truncateText(text,len=150){
    if(!text) return ""
    return text.length>len ? text.substring(0,len)+"…" : text
  }

  function formatTime(iso){
    if(!iso) return ""
    try{ return new Date(iso).toLocaleString() }catch{ return iso }
  }

  function loadAuditSummary(){
    axios.get("/api/admin/audit/summary").then(r=>setAuditData(r.data)).catch(()=>setAuditData(null))
  }

  function loadAuditPosts(offset=0, status="", subreddit=""){
    setAuditLoading(true)
    const params = new URLSearchParams({limit:"50", offset:String(offset)})
    if(status) params.set("status_filter", status)
    if(subreddit) params.set("subreddit", subreddit)
    axios.get(`/api/admin/audit/posts?${params.toString()}`)
      .then(r=>{ setAuditPosts(r.data.posts); setAuditLoading(false) })
      .catch(()=>{ setAuditPosts([]); setAuditLoading(false) })
  }

  function loadAuditPostDetail(postId){
    axios.get(`/api/admin/audit/post/${postId}`).then(r=>setAuditPostDetail(r.data)).catch(()=>setAuditPostDetail(null))
  }

  // Infinite scroll with cleanup
  useEffect(()=>{
    const obs = new IntersectionObserver(entries=>{
      if(entries[0].isIntersecting && !searchResults) load()
    })
    if(loader.current) obs.observe(loader.current)
    return ()=> obs.disconnect()
  },[loader.current, searchResults])

  // Archive infinite scroll
  useEffect(()=>{
    const obs = new IntersectionObserver(entries=>{
      if(entries[0].isIntersecting && !archiveSearchResults) loadArchive()
    })
    if(archiveLoader.current) obs.observe(archiveLoader.current)
    return ()=> obs.disconnect()
  },[archiveLoader.current, archiveSearchResults])

  useEffect(()=>{
    if(activeTab==="archive" && archivePosts.length===0 && archiveOffsetRef.current===0){
      loadArchive()
    }
  },[activeTab])

  function handleSearch(e){
    setSearch(e.target.value)
    clearTimeout(searchTimeout.current)
    if(!e.target.value.trim()){ setSearchResults(null); return }
    searchTimeout.current = setTimeout(()=>{
      axios.get(`/api/search?q=${encodeURIComponent(e.target.value)}`)
        .then(r=>{
          setSearchResults(r.data.map(p=>({
            id:p.id, title:p.title, subreddit:p.subreddit, author:p.author,
            created_utc:p.created_utc, image_url:p.image_url, video_url:p.video_url,
            thumb_url:p.thumb_url, is_video:p.is_video,
          })))
        })
    },300)
  }

  const LiveDot = ({connected}) => (
    <div title={connected?"Connected":"Connecting…"} style={{display:"flex",alignItems:"center",justifyContent:"center"}}>
      <div style={{
        width:"8px",height:"8px",borderRadius:"50%",
        background:connected?"#46d160":"#3a5068",
        boxShadow:connected?"0 0 6px #46d160":"none",
        animation:connected?"pulse 2s ease-in-out infinite":"none"
      }}/>
      <style>{`@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.4}}`}</style>
    </div>
  )

  const handleTouchStart = (e) => { if (e.touches.length === 1) setSwipeStart(e.touches[0].clientX) }
  const handleTouchMove = (e) => {
    if (!swipeStart || !selectedPost?.image_urls?.length) return
    const delta = e.touches[0].clientX - swipeStart
    if (Math.abs(delta) > 50) {
      if (delta > 0 && galleryIdx > 0) setGalleryIdx(i => i - 1)
      else if (delta < 0 && galleryIdx < selectedPost.image_urls.length - 1) setGalleryIdx(i => i + 1)
      setSwipeStart(null)
    }
  }
  const handleTouchEnd = () => setSwipeStart(null)

  // Mini bar chart
  function PostsChart({data}){
    if(!data || data.length === 0) return null
    const max = Math.max(...data.map(d=>d.count), 1)
    return (
      <div style={{marginBottom:"40px"}}>
        <div style={{display:"flex",alignItems:"center",gap:"12px",marginBottom:"16px"}}>
          <div style={{width:"4px",height:"24px",background:"linear-gradient(180deg,#35c5f4,#5fd4f8)",borderRadius:"2px"}} />
          <h2 style={{margin:0,fontSize:"20px",fontWeight:"600"}}>Posts (Last 7 Days)</h2>
        </div>
        <div style={{background:"linear-gradient(145deg,#1e1e1e,#171717)",borderRadius:"3px",border:"1px solid #2a2a2a",padding:"20px"}}>
          <div style={{display:"flex",alignItems:"flex-end",gap:"8px",height:"80px"}}>
            {data.map(d=>(
              <div key={d.date} style={{flex:1,display:"flex",flexDirection:"column",alignItems:"center",gap:"4px"}}>
                <span style={{fontSize:"10px",color:"#5a7b9a",fontVariantNumeric:"tabular-nums"}}>{d.count}</span>
                <div style={{width:"100%",height:`${Math.round((d.count/max)*60)+4}px`,background:"linear-gradient(180deg,#35c5f4,#5fd4f8)",borderRadius:"2px 2px 0 0",minHeight:"4px"}}/>
                <span style={{fontSize:"9px",color:"#3a5068",whiteSpace:"nowrap"}}>{d.date.slice(5)}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    )
  }

  // ── HELPERS ──
  const targets = adminData?.targets || []
  const subredditTargets = targets.filter(t => t.type === "subreddit")
  const userTargets = targets.filter(t => t.type === "user")

  // Get the current target info for detail view
  const currentTarget = targetDetailType && targetDetailName
    ? targets.find(t => t.type === targetDetailType && t.name.toLowerCase() === targetDetailName.toLowerCase())
    : null

  // Sidebar nav items (Sonarr/Radarr style)
  const sidebarWidth = sidebarCollapsed ? 60 : 200
  const navItems = [
    {to:"/library",label:"All Posts",icon:(<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>)},
    {to:"/subreddits",label:"Subreddits",match:"subreddits",icon:(<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><line x1="9" y1="9" x2="9.01" y2="9"/><line x1="15" y1="9" x2="15.01" y2="9"/></svg>),count:subredditTargets.length},
    {to:"/users",label:"Users",match:"users",icon:(<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>),count:userTargets.length},
    role === "admin" && {to:"/archive",label:"Hidden",icon:(<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 8v13H3V8"/><path d="M1 3h22v5H1z"/><path d="M10 12h4"/></svg>)},
    role === "admin" && {to:"/wanted",label:"Wanted",icon:(<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>)},
    role === "admin" && {to:"/system",label:"System",icon:(<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>)},
    role === "admin" && {to:"/activity",label:"Activity",icon:(<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>)},
    role === "admin" && {to:"/logs",label:"Logs",icon:(<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>)},
  ].filter(Boolean)

  // ── Shared components ──

  // Post card (shared between library, archive, target detail)
  function PostCard({p, isArchive, onClick}){
    return (
      <article
        onClick={onClick || (()=>{setGalleryIdx(0);setSelectedPost(p)})}
        onKeyDown={e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();setGalleryIdx(0);setSelectedPost(p)}}}
        onMouseEnter={()=>setHoveredCard(p.id)} onMouseLeave={()=>setHoveredCard(null)}
        role="button" tabIndex={0} aria-label={p.title}
        className="post-card"
        style={{background:"linear-gradient(145deg,#1e1e1e,#171717)",borderRadius:"3px",overflow:"hidden",cursor:"pointer",border:"1px solid #2a2a2a",opacity:isArchive?0.9:1}}>
        {p.is_video ? (
          <div style={{aspectRatio:"1",background:"#0a0a0a",position:"relative",overflow:"hidden"}}>
            {hoveredCard===p.id && p.video_url && (p.video_url.includes("v.redd.it")||p.video_url.endsWith(".mp4")) ? (
              <video src={p.video_url} autoPlay muted loop playsInline style={{width:"100%",height:"100%",objectFit:"cover"}}/>
            ) : (
              <div style={{width:"100%",height:"100%",display:"flex",alignItems:"center",justifyContent:"center",background:"linear-gradient(135deg,#111 0%,#1a1a1a 100%)",position:"relative"}}>
                {(p.thumb_url||p.preview_url) && <img src={p.thumb_url||p.preview_url} alt="" loading="lazy" decoding="async" style={{position:"absolute",inset:0,width:"100%",height:"100%",objectFit:"cover",opacity:0.7}} onError={e=>e.target.style.display="none"}/>}
                <div style={{position:"relative",zIndex:1,width:"64px",height:"64px",borderRadius:"50%",background:"rgba(0,0,0,0.55)",border:"2px solid rgba(255,69,0,0.7)",display:"flex",alignItems:"center",justifyContent:"center",transition:"transform 0.2s",transform:hoveredCard===p.id?"scale(1.1)":"scale(1)",backdropFilter:"blur(2px)"}}>
                  <div style={{width:0,height:0,borderTop:"12px solid transparent",borderBottom:"12px solid transparent",borderLeft:"20px solid #35c5f4",marginLeft:"4px"}}/>
                </div>
              </div>
            )}
            <div style={{position:"absolute",top:"10px",left:"10px",background:"rgba(0,0,0,0.75)",backdropFilter:"blur(4px)",borderRadius:"3px",padding:"3px 8px",display:"flex",alignItems:"center",gap:"5px",fontSize:"10px",fontWeight:"700",color:"#f5f7fa",letterSpacing:"0.5px",border:"1px solid rgba(255,255,255,0.1)"}}>
              <div style={{width:0,height:0,borderTop:"5px solid transparent",borderBottom:"5px solid transparent",borderLeft:"8px solid #35c5f4"}}/>VIDEO
            </div>
            {isArchive && <div style={{position:"absolute",top:"10px",right:"10px",background:"rgba(0,0,0,0.75)",borderRadius:"3px",padding:"2px 6px",fontSize:"9px",color:"#5a7b9a",fontWeight:"600"}}>ARCHIVED</div>}
            <div style={{position:"absolute",bottom:0,left:0,right:0,background:"linear-gradient(transparent,rgba(0,0,0,0.8))",padding:"40px 16px 16px"}}>
              <div style={{fontSize:"11px",color:isArchive?"#8aa4bd":"#35c5f4",textTransform:"uppercase",letterSpacing:"1px",fontWeight:"600"}}>{p.subreddit||"reddit"}</div>
            </div>
          </div>
        ) : (p.url || p.image_urls?.[0]) ? (
          <div style={{aspectRatio:"1",background:"#131b2e",position:"relative",overflow:"hidden"}}>
            <img src={p.url||p.image_urls?.[0]} alt={p.title} loading="lazy" decoding="async" style={{width:"100%",height:"100%",objectFit:"cover",opacity:isArchive?0.85:1}} onError={e=>e.target.style.display="none"}/>
            {p.image_urls?.length > 1 && (
              <div style={{position:"absolute",top:"10px",right:"10px",background:"rgba(0,0,0,0.75)",backdropFilter:"blur(4px)",borderRadius:"3px",padding:"4px 10px",fontSize:"11px",fontWeight:"600",color:"#f5f7fa"}}>1/{p.image_urls.length}</div>
            )}
            {isArchive && <div style={{position:"absolute",top:"10px",left:"10px",background:"rgba(0,0,0,0.75)",borderRadius:"3px",padding:"2px 6px",fontSize:"9px",color:"#5a7b9a",fontWeight:"600"}}>ARCHIVED</div>}
            <div style={{position:"absolute",bottom:0,left:0,right:0,background:"linear-gradient(transparent,rgba(0,0,0,0.8))",padding:"40px 16px 16px"}}>
              <div style={{fontSize:"11px",color:isArchive?"#8aa4bd":"#35c5f4",textTransform:"uppercase",letterSpacing:"1px",fontWeight:"600"}}>{p.subreddit||"reddit"}</div>
            </div>
          </div>
        ) : (
          <div style={{padding:"24px",background:"linear-gradient(135deg,#1a1a1a 0%,#222 100%)",minHeight:"180px",display:"flex",flexDirection:"column"}}>
            <div style={{fontSize:"11px",color:isArchive?"#8aa4bd":"#35c5f4",textTransform:"uppercase",letterSpacing:"1px",fontWeight:"600",marginBottom:"12px"}}>{p.subreddit||"reddit"}</div>
            <div style={{fontSize:"16px",fontWeight:"600",marginBottom:"12px",lineHeight:"1.4",color:"#f5f7fa"}}>{p.title}</div>
            {p.selftext && <div style={{fontSize:"13px",color:"#7a96ad",lineHeight:"1.6",flex:1}}>{truncateText(p.selftext)}</div>}
          </div>
        )}
        <div style={{padding:"10px 14px",display:"flex",justifyContent:"space-between",alignItems:"center",gap:"8px"}}>
          <div style={{minWidth:0,flex:1}}>
            <div style={{fontSize:"10px",color:"#5a7b9a",textTransform:"uppercase",letterSpacing:"1px",marginBottom:"3px"}}>{p.subreddit||"reddit"}</div>
            <div style={{fontSize:"13px",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",color:isArchive?"#8aa4bd":"#c8d6e0"}}>{p.title}</div>
          </div>
          {role === "admin" && (
            <div style={{display:"flex",gap:"4px",flexShrink:0}}>
              <button onClick={e=>{e.stopPropagation();deletePost(p.id)}} aria-label="Delete post"
                style={{minWidth:"36px",minHeight:"36px",padding:"0 8px",background:"#161d2f",border:"1px solid #333",borderRadius:"3px",color:"#5a7b9a",cursor:"pointer",fontSize:"14px",display:"flex",alignItems:"center",justifyContent:"center",transition:"background 0.15s, color 0.15s"}}>
                <span aria-hidden="true">🗑</span>
              </button>
              <button onClick={e=>{e.stopPropagation();isArchive?unhidePost(p.id):hidePost(p.id)}} aria-label={isArchive?"Unhide":"Hide"}
                style={{minWidth:"36px",minHeight:"36px",padding:"0 8px",background:"#161d2f",border:"1px solid #333",borderRadius:"3px",color:"#5a7b9a",cursor:"pointer",fontSize:"14px",display:"flex",alignItems:"center",justifyContent:"center",transition:"background 0.15s, color 0.15s"}}>
                <span aria-hidden="true">👁</span>
              </button>
            </div>
          )}
        </div>
      </article>
    )
  }

  // View mode toggle
  function ViewToggle(){
    return (
      <div style={{display:"flex",border:"1px solid #2a2a2a",borderRadius:"3px",overflow:"hidden"}}>
        <button onClick={()=>setViewMode("grid")} style={{padding:"6px 10px",background:viewMode==="grid"?"#35c5f4":"#161d2f",color:viewMode==="grid"?"#fff":"#5a7b9a",border:"none",cursor:"pointer",fontSize:"14px"}}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>
        </button>
        <button onClick={()=>setViewMode("table")} style={{padding:"6px 10px",background:viewMode==="table"?"#35c5f4":"#161d2f",color:viewMode==="table"?"#fff":"#5a7b9a",border:"none",cursor:"pointer",fontSize:"14px",borderLeft:"1px solid #2a2a2a"}}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>
        </button>
      </div>
    )
  }

  // ── Target card for subreddit/user index pages (Sonarr poster style) ──
  function TargetCard({t}){
    const mediaPct = t.total_media > 0 ? Math.round((t.downloaded_media / t.total_media) * 100) : 0
    const prefix = t.type==="subreddit"?"r/":"u/"
    const detailPath = t.type==="subreddit"?`/subreddits/${encodeURIComponent(t.name)}`:`/users/${encodeURIComponent(t.name)}`
    return (
      <div
        onClick={()=>navigate(detailPath)}
        style={{background:"linear-gradient(145deg,#1e1e1e,#171717)",borderRadius:"3px",border:t.status==="taken_down"?"1px solid #ff000044":t.status==="deleted"?"1px solid #ffff0044":"1px solid #2a2a2a",overflow:"hidden",cursor:"pointer",opacity:t.enabled?1:0.6,transition:"all 0.2s",display:"flex",flexDirection:"column"}}
      >
        {/* Poster area */}
        <div style={{aspectRatio:"2/3",background:"linear-gradient(135deg,#0b1728,#131b2e)",display:"flex",alignItems:"center",justifyContent:"center",position:"relative",overflow:"hidden"}}>
          {t.icon_url ? (
            <img src={t.icon_url} alt="" loading="lazy" decoding="async" style={{width:"100%",height:"100%",objectFit:"cover"}} onError={e=>{e.target.style.display="none";e.target.nextSibling.style.display="flex"}}/>
          ) : null}
          <div style={{fontSize:"48px",fontWeight:"900",color:"#1c2a3f",letterSpacing:"-2px",display:t.icon_url?"none":"flex",alignItems:"center",justifyContent:"center",position:"absolute",inset:0}}>{prefix}{t.name.slice(0,2).toUpperCase()}</div>
          {/* Status badge */}
          {t.status !== "active" && (
            <div style={{position:"absolute",top:"8px",right:"8px",padding:"2px 8px",borderRadius:"3px",fontSize:"9px",fontWeight:"700",background:t.status==="taken_down"?"#440000":"#444400",color:t.status==="taken_down"?"#ff4444":"#ffff44"}}>
              {t.status==="taken_down"?"BANNED":t.status==="deleted"?"DELETED":t.status.toUpperCase()}
            </div>
          )}
          {!t.enabled && <div style={{position:"absolute",top:"8px",left:"8px",padding:"2px 8px",borderRadius:"3px",fontSize:"9px",fontWeight:"700",background:"#333",color:"#888"}}>DISABLED</div>}
          {/* Media progress bar at bottom of poster */}
          <div style={{position:"absolute",bottom:0,left:0,right:0,height:"4px",background:"#0b1728"}}>
            <div style={{width:`${Math.min(100,mediaPct)}%`,height:"100%",background:mediaPct>=100?"#46d160":"linear-gradient(90deg,#35c5f4,#5fd4f8)",transition:"width 0.3s"}}/>
          </div>
        </div>
        {/* Info */}
        <div style={{padding:"12px",display:"flex",flexDirection:"column",gap:"6px",flex:1}}>
          <div style={{fontSize:"14px",fontWeight:"600",color:t.status==="active"?"#f5f7fa":t.status==="taken_down"?"#ff6666":"#8aa4bd",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>
            {prefix}{t.name}
          </div>
          <div style={{display:"flex",gap:"12px",fontSize:"11px",color:"#5a7b9a"}}>
            <span>{t.post_count?.toLocaleString()} posts</span>
            <span style={{color:"#46d160"}}>{t.downloaded_media}/{t.total_media}</span>
          </div>
        </div>
      </div>
    )
  }

  // ── Target table row for subreddit/user index pages ──
  function TargetRow({t}){
    const mediaPct = t.total_media > 0 ? Math.round((t.downloaded_media / t.total_media) * 100) : 0
    const prefix = t.type==="subreddit"?"r/":"u/"
    const detailPath = t.type==="subreddit"?`/subreddits/${encodeURIComponent(t.name)}`:`/users/${encodeURIComponent(t.name)}`
    return (
      <tr onClick={()=>navigate(detailPath)} style={{cursor:"pointer",borderBottom:"1px solid #222",transition:"background 0.15s"}} onMouseEnter={e=>e.currentTarget.style.background="#161d2f"} onMouseLeave={e=>e.currentTarget.style.background="transparent"}>
        <td style={{padding:"12px 16px"}}>
          {t.enabled
            ? <span style={{background:"#0d2818",color:"#46d160",padding:"3px 8px",borderRadius:"3px",fontSize:"11px"}}>● Active</span>
            : <span style={{background:"#333",color:"#888",padding:"3px 8px",borderRadius:"3px",fontSize:"11px"}}>○ Off</span>}
          {t.status !== "active" && <span style={{marginLeft:"6px",fontSize:"10px",color:t.status==="taken_down"?"#ff4444":"#ffff44"}}>{t.status}</span>}
        </td>
        <td style={{padding:"12px 16px",fontWeight:"600",color:"#f5f7fa"}}>{prefix}{t.name}</td>
        <td style={{padding:"12px 16px",fontVariantNumeric:"tabular-nums"}}>{t.post_count?.toLocaleString()}</td>
        <td style={{padding:"12px 16px"}}>
          <div style={{display:"flex",alignItems:"center",gap:"8px"}}>
            <div style={{flex:1,background:"#0b1728",height:"6px",borderRadius:"3px",overflow:"hidden",maxWidth:"120px"}}>
              <div style={{width:`${Math.min(100,mediaPct)}%`,height:"100%",background:mediaPct>=100?"#46d160":"linear-gradient(90deg,#35c5f4,#5fd4f8)"}}/>
            </div>
            <span style={{fontSize:"11px",color:"#5a7b9a",fontVariantNumeric:"tabular-nums"}}>{t.downloaded_media}/{t.total_media}</span>
          </div>
        </td>
        <td style={{padding:"12px 16px",color:"#5a7b9a",fontSize:"12px"}}>{t.last_created?new Date(t.last_created).toLocaleDateString():"—"}</td>
        <td style={{padding:"12px 16px",color:"#5a7b9a",fontSize:"12px"}}>{formatRate(t.rate_per_second)}</td>
      </tr>
    )
  }

  // ── Page title helper ──
  function pageTitle(){
    if(targetDetailType && targetDetailName) return `${targetDetailType==="subreddit"?"r/":"u/"}${targetDetailName}`
    switch(activeTab){
      case "library": return "All Posts"
      case "subreddits": return "Subreddits"
      case "users": return "Users"
      case "archive": return "Hidden"
      case "wanted": return "Wanted"
      case "system": return "System"
      case "activity": return "Activity"
      case "logs": return "Logs"
      default: return "Reddarr"
    }
  }

  if (!token) {
    const doLogin = (e) => {
      e.preventDefault()
      setLoginErr("")
      axios.post("/api/login", {username: loginUser, password: loginPass})
        .then(r => {
          localStorage.setItem("token", r.data.token)
          localStorage.setItem("role", r.data.role)
          setToken(r.data.token)
          setRole(r.data.role)
        })
        .catch(e => setLoginErr(e.response?.data?.detail || "Login failed"))
    }
    return (
      <div style={{display:"flex",minHeight:"100vh",background:"#1a2234",alignItems:"center",justifyContent:"center",color:"#dfe6ed",fontFamily:"-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Oxygen,Ubuntu,sans-serif"}}>
        <form onSubmit={doLogin} style={{background:"#161d2f",padding:"40px",borderRadius:"5px",border:"1px solid #1c2a3f",width:"100%",maxWidth:"320px",display:"flex",flexDirection:"column",gap:"15px",boxShadow:"0 10px 30px rgba(0,0,0,0.5)"}}>
          <h2 style={{margin:"0 0 10px",textAlign:"center",fontSize:"22px",color:"#f5f7fa"}}>Reddarr Login</h2>
          {loginErr && <div style={{color:"#ff6666",fontSize:"13px",textAlign:"center"}}>{loginErr}</div>}
          <input type="text" placeholder="Username" value={loginUser} onChange={e=>setLoginUser(e.target.value)} style={{padding:"12px",borderRadius:"3px",border:"1px solid #2a2a2a",background:"#0b1728",color:"#f5f7fa",fontSize:"14px",outline:"none"}}/>
          <input type="password" placeholder="Password" value={loginPass} onChange={e=>setLoginPass(e.target.value)} style={{padding:"12px",borderRadius:"3px",border:"1px solid #2a2a2a",background:"#0b1728",color:"#f5f7fa",fontSize:"14px",outline:"none"}}/>
          <button type="submit" style={{padding:"12px",background:"linear-gradient(135deg,#35c5f4,#5fd4f8)",border:"none",borderRadius:"3px",color:"#0b1728",fontWeight:"bold",cursor:"pointer",fontSize:"15px",marginTop:"10px"}}>Log In</button>
        </form>
      </div>
    )
  }

  return (
    <div style={{display:"flex",minHeight:"100vh",background:"#1a2234",color:"#dfe6ed",fontFamily:"-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Oxygen,Ubuntu,sans-serif"}}>
      {/* ── SIDEBAR (Sonarr-style) ── */}
      <nav style={{
        width:`${sidebarWidth}px`,
        minHeight:"100vh",
        background:"#0b1728",
        borderRight:"1px solid #1c2a3f",
        display:"flex",
        flexDirection:"column",
        padding:"0",
        position:"fixed",
        top:0,left:0,
        zIndex:100,
        transition:"width 0.2s ease",
        overflow:"hidden",
      }}>
        {/* Logo */}
        <div style={{padding:"16px",display:"flex",alignItems:"center",gap:"12px",borderBottom:"1px solid #1c2a3f",minHeight:"60px",cursor:"pointer"}} onClick={()=>setSidebarCollapsed(c=>!c)}>
          <div style={{width:"36px",height:"36px",borderRadius:"3px",background:"linear-gradient(135deg,#35c5f4,#2196f3)",display:"flex",alignItems:"center",justifyContent:"center",fontWeight:"900",fontSize:"16px",color:"#f5f7fa",letterSpacing:"-1px",flexShrink:0}}>R</div>
          {!sidebarCollapsed && <span style={{fontSize:"18px",fontWeight:"700",color:"#f5f7fa",letterSpacing:"-0.5px",whiteSpace:"nowrap"}}>Reddarr</span>}
        </div>
        {/* Nav links */}
        <div style={{display:"flex",flexDirection:"column",gap:"2px",padding:"12px 8px",flex:1}}>
          {navItems.map(item=>{
            const isActive = item.match ? activeTab === item.match : activeTab === item.to.slice(1)
            return (
              <NavLink key={item.to} to={item.to} style={()=>({
                height:"40px",
                borderRadius:"3px",
                display:"flex",alignItems:"center",gap:"12px",
                padding:sidebarCollapsed?"0 0 0 13px":"0 12px",
                background:isActive?"rgba(53,197,244,0.15)":"transparent",
                color:isActive?"#35c5f4":"#5a7b9a",
                cursor:"pointer",
                transition:"all 0.15s ease",
                textDecoration:"none",
                position:"relative",
                borderLeft:isActive?"3px solid #35c5f4":"3px solid transparent",
                whiteSpace:"nowrap",
                overflow:"hidden",
              })} title={sidebarCollapsed?item.label:undefined}>
                <span style={{flexShrink:0,display:"flex"}}>{item.icon}</span>
                {!sidebarCollapsed && <span style={{fontSize:"13px",fontWeight:isActive?"600":"400"}}>{item.label}</span>}
                {!sidebarCollapsed && item.count !== undefined && (
                  <span style={{marginLeft:"auto",fontSize:"11px",background:"#1c2a3f",padding:"1px 6px",borderRadius:"3px",color:"#5a7b9a",fontVariantNumeric:"tabular-nums"}}>{item.count}</span>
                )}
              </NavLink>
            )
          })}
        </div>
        {/* Bottom status */}
        <div style={{padding:"16px",display:"flex",alignItems:"center",gap:"10px",borderTop:"1px solid #1c2a3f"}}>
          <LiveDot connected={liveConnected}/>
          {!sidebarCollapsed && (
            <div style={{fontSize:"11px",color:"#3a5068",flex:1,display:"flex",justifyContent:"space-between",alignItems:"center"}}>
              <div>
                {liveConnected?"Connected":"Connecting…"}
                {queueInfo && <span style={{marginLeft:"8px",color:queueInfo.queue_length>0?"#f9c300":"#46d160"}}>{queueInfo.queue_length||0}Q</span>}
              </div>
              <button onClick={()=>{setToken(null);setRole(null);localStorage.clear();window.location.reload()}} style={{background:"none",border:"none",color:"#5a7b9a",cursor:"pointer",textDecoration:"underline",fontSize:"11px",padding:0}}>Logout</button>
            </div>
          )}
        </div>
      </nav>

      {/* ── MAIN CONTENT ── */}
      <div style={{flex:1,marginLeft:`${sidebarWidth}px`,minHeight:"100vh",display:"flex",flexDirection:"column",transition:"margin-left 0.2s ease"}}>
        {/* Top toolbar */}
        <header style={{
          padding:"0 24px",height:"50px",background:"#161d2f",borderBottom:"1px solid #1c2a3f",
          display:"flex",alignItems:"center",justifyContent:"space-between",position:"sticky",top:0,zIndex:90,
        }}>
          <div style={{display:"flex",alignItems:"center",gap:"16px"}}>
            {/* Back button for detail views */}
            {targetDetailType && (
              <button onClick={()=>navigate(targetDetailType==="subreddit"?"/subreddits":"/users")} style={{padding:"4px 8px",background:"transparent",border:"1px solid #333",borderRadius:"3px",color:"#5a7b9a",cursor:"pointer",fontSize:"16px",lineHeight:1}}>←</button>
            )}
            <h1 style={{margin:0,fontSize:"18px",fontWeight:"700",color:"#f5f7fa",letterSpacing:"-0.5px"}}>{pageTitle()}</h1>
            {queueInfo && (
              <div style={{fontSize:"12px",color:"#5a7b9a",display:"flex",alignItems:"center",gap:"6px"}}>
                <span>Queue:</span>
                <span style={{color:queueInfo.queue_length>0?"#f9c300":"#46d160",fontWeight:"600",fontVariantNumeric:"tabular-nums"}}>{(queueInfo.queue_length||0).toLocaleString()}</span>
              </div>
            )}
          </div>
          <div style={{display:"flex",alignItems:"center",gap:"12px"}}>
            {/* View toggle on index pages */}
            {(activeTab === "subreddits" || activeTab === "users") && !targetDetailType && <ViewToggle/>}
            <div style={{position:"relative"}}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#5a7b9a" strokeWidth="2" style={{position:"absolute",left:"12px",top:"50%",transform:"translateY(-50%)"}}><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
              <input type="search" inputMode="search" enterKeyHint="search" placeholder="Search…" aria-label="Search posts" autoComplete="off" spellCheck={false} value={search} onChange={handleSearch}
                style={{padding:"8px 12px 8px 36px",borderRadius:"3px",border:"1px solid #1c2a3f",width:"220px",background:"#0b1728",color:"#dfe6ed",fontSize:"13px",outline:"none"}}/>
            </div>
          </div>
        </header>

        {/* ── SUBREDDITS / USERS INDEX PAGE ── */}
        {(activeTab === "subreddits" || activeTab === "users") && !targetDetailType && (()=>{
          const items = activeTab === "subreddits" ? subredditTargets : userTargets
          const typeLabel = activeTab === "subreddits" ? "subreddit" : "user"
          return (
            <div style={{padding:"24px",maxWidth:"1400px",margin:"0 auto",width:"100%"}}>
              {/* Action bar */}
              <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:"24px",flexWrap:"wrap",gap:"12px"}}>
                <div style={{display:"flex",alignItems:"center",gap:"12px"}}>
                  <div style={{width:"4px",height:"24px",background:"linear-gradient(180deg,#35c5f4,#5fd4f8)",borderRadius:"2px"}}/>
                  <span style={{fontSize:"14px",color:"#5a7b9a"}}>{items.length} {typeLabel}{items.length!==1?"s":""}</span>
                </div>
                {role === "admin" && (
                  <div style={{display:"flex",gap:"8px",alignItems:"center"}}>
                    <input type="text" placeholder={`Add ${typeLabel}…`} aria-label={`Add ${typeLabel}`} autoComplete="off" spellCheck={false} value={addTargetType===typeLabel?addTargetName:""} onChange={e=>{setAddTargetType(typeLabel);setAddTargetName(e.target.value)}}
                      onKeyDown={e=>{if(e.key==="Enter"){setAddTargetType(typeLabel);addTarget()}}}
                      style={{padding:"8px 12px",background:"#0b1728",border:"1px solid #1c2a3f",borderRadius:"3px",color:"#f5f7fa",fontSize:"13px",outline:"none",width:"180px"}}/>
                    <button onClick={()=>{setAddTargetType(typeLabel);addTarget()}} disabled={!addTargetName.trim()||addTargetType!==typeLabel}
                      style={{padding:"8px 16px",background:addTargetName.trim()&&addTargetType===typeLabel?"linear-gradient(135deg,#35c5f4,#5fd4f8)":"#243447",border:"none",borderRadius:"3px",color:addTargetName.trim()&&addTargetType===typeLabel?"#f5f7fa":"#5a7b9a",cursor:addTargetName.trim()?"pointer":"not-allowed",fontSize:"13px",fontWeight:"600"}}>
                      + Add
                    </button>
                  </div>
                )}
              </div>

              {items.length === 0 && (
                <div style={{padding:"60px",textAlign:"center",color:"#5a7b9a"}}>
                  No {typeLabel}s tracked yet. Add one above.
                </div>
              )}

              {/* Grid view */}
              {viewMode === "grid" && items.length > 0 && (
                <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,minmax(160px,1fr))",gap:"16px"}}>
                  {items.map(t=><TargetCard key={`${t.type}-${t.name}`} t={t}/>)}
                </div>
              )}

              {/* Table view */}
              {viewMode === "table" && items.length > 0 && (
                <div style={{background:"#1c2a3f",borderRadius:"3px",border:"1px solid #2a2a2a",overflow:"hidden"}}>
                  <table style={{width:"100%",borderCollapse:"collapse",fontSize:"13px"}}>
                    <thead><tr style={{background:"#131b2e"}}>
                      {["Status","Name","Posts","Media","Last Post","Rate"].map(h=>(
                        <th key={h} style={{padding:"12px 16px",textAlign:"left",color:"#5a7b9a",fontSize:"11px",textTransform:"uppercase",letterSpacing:"0.5px"}}>{h}</th>
                      ))}
                    </tr></thead>
                    <tbody>
                      {items.map(t=><TargetRow key={`${t.type}-${t.name}`} t={t}/>)}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )
        })()}

        {/* ── TARGET DETAIL PAGE ── */}
        {targetDetailType && targetDetailName && (
          <div style={{padding:"24px",maxWidth:"1400px",margin:"0 auto",width:"100%"}}>
            {/* Target info header */}
            {currentTarget && (
              <div style={{background:"linear-gradient(145deg,#1e1e1e,#171717)",borderRadius:"3px",border:"1px solid #2a2a2a",padding:"20px",marginBottom:"24px",display:"flex",gap:"20px",alignItems:"flex-start",flexWrap:"wrap"}}>
                {/* Poster */}
                <div style={{width:"120px",height:"180px",background:"linear-gradient(135deg,#0b1728,#131b2e)",borderRadius:"3px",display:"flex",alignItems:"center",justifyContent:"center",flexShrink:0}}>
                  <div style={{fontSize:"32px",fontWeight:"900",color:"#1c2a3f"}}>{(targetDetailType==="subreddit"?"r/":"u/") + currentTarget.name.slice(0,2).toUpperCase()}</div>
                </div>
                {/* Info */}
                <div style={{flex:1,minWidth:0}}>
                  <div style={{fontSize:"24px",fontWeight:"700",color:"#f5f7fa",marginBottom:"8px"}}>{targetDetailType==="subreddit"?"r/":"u/"}{currentTarget.name}</div>
                  <div style={{display:"flex",gap:"16px",fontSize:"13px",color:"#5a7b9a",marginBottom:"16px",flexWrap:"wrap"}}>
                    <span>{currentTarget.post_count?.toLocaleString()} posts</span>
                    <span style={{color:"#46d160"}}>{currentTarget.downloaded_media}/{currentTarget.total_media} media</span>
                    <span>{formatRate(currentTarget.rate_per_second)}</span>
                    {currentTarget.status !== "active" && <span style={{color:"#ff6666"}}>{currentTarget.status}</span>}
                  </div>
                  {/* Progress bar */}
                  <div style={{background:"#0b1728",height:"6px",borderRadius:"3px",overflow:"hidden",maxWidth:"400px",marginBottom:"16px"}}>
                    <div style={{width:`${currentTarget.total_media>0?Math.min(100,Math.round(currentTarget.downloaded_media/currentTarget.total_media*100)):0}%`,height:"100%",background:"linear-gradient(90deg,#35c5f4,#5fd4f8)",transition:"width 0.3s"}}/>
                  </div>
                  {/* Action buttons */}
                  {role === "admin" && (
                    <div style={{display:"flex",gap:"8px",flexWrap:"wrap"}}>
                      <button onClick={()=>toggleTarget(currentTarget.type,currentTarget.name)} style={{padding:"6px 14px",background:currentTarget.enabled?"#46d160":"#3a3a3a",border:"none",borderRadius:"3px",color:currentTarget.enabled?"#000":"#5a7b9a",cursor:"pointer",fontSize:"12px",fontWeight:"600"}}>
                        {currentTarget.enabled?"Enabled":"Disabled"}
                      </button>
                      <div style={{position:"relative",display:"inline-block"}}>
                        <button onClick={()=>{const m=document.getElementById(`sync-menu-${currentTarget.name}`);m.style.display=m.style.display==="none"?"block":"none"}} style={{padding:"6px 14px",background:"linear-gradient(135deg,#35c5f4,#5fd4f8)",border:"none",borderRadius:"3px",color:"#f5f7fa",cursor:"pointer",fontSize:"12px",fontWeight:"600"}}>⚡ Sync ▼</button>
                        <div id={`sync-menu-${currentTarget.name}`} style={{display:"none",position:"absolute",top:"100%",left:0,zIndex:100,background:"#1a2234",border:"1px solid #333",borderRadius:"3px",minWidth:"140px",marginTop:"4px",boxShadow:"0 4px 12px rgba(0,0,0,0.4)"}}>
                          <div onClick={()=>{document.getElementById(`sync-menu-${currentTarget.name}`).style.display="none";scrapeTargetNow(currentTarget.type,currentTarget.name)}} style={{padding:"10px 14px",cursor:"pointer",color:"#f5f7fa",fontSize:"12px",borderBottom:"1px solid #222"}}>Get Latest</div>
                          <div onClick={()=>{document.getElementById(`sync-menu-${currentTarget.name}`).style.display="none";backfillTargetNow(currentTarget.type,currentTarget.name)}} style={{padding:"10px 14px",cursor:"pointer",color:"#7ab3e0",fontSize:"12px"}}>Get History</div>
                        </div>
                      </div>
                      <div style={{position:"relative",display:"inline-block"}}>
                        <button onClick={()=>{const m=document.getElementById(`dl-menu-${currentTarget.name}`);m.style.display=m.style.display==="none"?"block":"none"}} style={{padding:"6px 14px",background:"#1e2a1e",border:"1px solid #2a4a2a",borderRadius:"3px",color:"#46d160",cursor:"pointer",fontSize:"12px",fontWeight:"600"}}>🔄 Download ▼</button>
                        <div id={`dl-menu-${currentTarget.name}`} style={{display:"none",position:"absolute",top:"100%",left:0,zIndex:100,background:"#1a2234",border:"1px solid #333",borderRadius:"3px",minWidth:"140px",marginTop:"4px",boxShadow:"0 4px 12px rgba(0,0,0,0.4)"}}>
                          <div onClick={()=>{document.getElementById(`dl-menu-${currentTarget.name}`).style.display="none";rescanTarget(currentTarget.type,currentTarget.name)}} style={{padding:"10px 14px",cursor:"pointer",color:"#46d160",fontSize:"12px",borderBottom:"1px solid #222"}}>All Media</div>
                          <div onClick={()=>{document.getElementById(`dl-menu-${currentTarget.name}`).style.display="none";rescrapeTargetNow(currentTarget.type,currentTarget.name)}} style={{padding:"10px 14px",cursor:"pointer",color:"#f9c300",fontSize:"12px"}}>Failed Only</div>
                        </div>
                      </div>
                      <button onClick={()=>{if(window.confirm(`Archive all posts from ${targetDetailType==="subreddit"?"r/":"u/"}${currentTarget.name}?`))runArchiveTarget(currentTarget.type,currentTarget.name)}} style={{padding:"6px 14px",background:"#132213",border:"1px solid #1a3a1a",borderRadius:"3px",color:"#46d160",cursor:"pointer",fontSize:"12px",fontWeight:"600"}}>📦 Archive</button>
                      <button onClick={()=>deleteTarget(currentTarget.type,currentTarget.name)} style={{padding:"6px 14px",background:"#2a0000",border:"1px solid #440000",borderRadius:"3px",color:"#ff4444",cursor:"pointer",fontSize:"12px",fontWeight:"600"}}>✕ Remove</button>
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Live Stats Panel */}
            {targetLiveStats && (
              <div style={{background:"linear-gradient(145deg,#1e1e1e,#171717)",borderRadius:"3px",border:"1px solid #2a2a2a",padding:"16px",marginBottom:"24px",display:"flex",gap:"16px",flexWrap:"wrap",alignItems:"center"}}>
                <div style={{fontSize:"11px",color:"#5a7b9a",textTransform:"uppercase",marginRight:"8px"}}>Live Stats</div>
                {[
                  {label:"Today",value:targetLiveStats.posts_today,color:"#35c5f4"},
                  {label:"This Week",value:targetLiveStats.posts_this_week,color:"#7ab3e0"},
                  {label:"Queued",value:targetLiveStats.queue_length,color:"#f9c300"},
                  {label:"Pending",value:targetLiveStats.pending_media,color:"#f9c300"},
                  {label:"Failed",value:targetLiveStats.failed_media,color:targetLiveStats.failed_media>0?"#ff6666":"#46d160"},
                  {label:"Error",value:targetLiveStats.error_media,color:targetLiveStats.error_media>0?"#ff6666":"#46d160"},
                ].map(s=>(
                  <div key={s.label} style={{background:"#161d2f",padding:"8px 14px",borderRadius:"3px",border:"1px solid #2a2a2a"}}>
                    <div style={{fontSize:"10px",color:"#5a7b9a",textTransform:"uppercase",marginBottom:"2px"}}>{s.label}</div>
                    <div style={{fontSize:"16px",fontWeight:"700",color:s.color,fontVariantNumeric:"tabular-nums"}}>{typeof s.value === "number" ? s.value.toLocaleString() : s.value}</div>
                  </div>
                ))}
                {targetLiveStats.last_posted_at && (
                  <div style={{fontSize:"11px",color:"#5a7b9a",marginLeft:"auto"}}>
                    Last post: {new Date(targetLiveStats.last_posted_at).toLocaleString()}
                  </div>
                )}
              </div>
            )}

            {/* Failures Panel */}
            {targetLiveStats && (targetLiveStats.failed_media > 0 || targetLiveStats.error_media > 0) && (
              <div style={{marginBottom:"24px"}}>
                <div onClick={()=>setTargetFailuresOpen(o=>!o)} style={{display:"flex",alignItems:"center",justifyContent:"space-between",padding:"12px 16px",background:targetFailuresOpen?"#2a1a1a":"#1c2a3f",borderRadius:"3px",border:"1px solid #3a2a2a",cursor:"pointer"}}>
                  <div style={{display:"flex",alignItems:"center",gap:"12px"}}>
                    <div style={{width:"4px",height:"20px",background:"linear-gradient(180deg,#ff4444,#cc0000)",borderRadius:"2px"}}/>
                    <h3 style={{margin:0,fontSize:"14px",fontWeight:"600",color:"#ff6666"}}>Failures ({targetLiveStats.failed_media + targetLiveStats.error_media})</h3>
                  </div>
                  <span style={{color:"#ff6666",fontSize:"14px",transform:targetFailuresOpen?"rotate(0deg)":"rotate(-90deg)",transition:"transform 0.2s"}}>▼</span>
                </div>
                {targetFailuresOpen && (
                  <div style={{background:"#161d2f",borderRadius:"0 0 3px 3px",border:"1px solid #3a2a2a",borderTop:"none",padding:"12px",maxHeight:"300px",overflow:"auto"}}>
                    {targetFailuresLoading ? (
                      <div style={{padding:"20px",textAlign:"center",color:"#5a7b9a"}}>Loading failures...</div>
                    ) : targetFailures.length === 0 ? (
                      <div style={{padding:"20px",textAlign:"center",color:"#5a7b9a"}}>No failures recorded.</div>
                    ) : (
                      <div style={{display:"flex",flexDirection:"column",gap:"8px"}}>
                        {targetFailures.map(f=>(
                          <div key={f.id} style={{background:"#0b1728",borderRadius:"3px",padding:"10px",border:"1px solid #2a2a2a"}}>
                            <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:"6px"}}>
                              <span style={{background:f.status==="failed"?"#3a1a1a":"#3a2a1a",color:f.status==="failed"?"#ff6666":"#ffaa00",padding:"2px 8px",borderRadius:"3px",fontSize:"10px",fontWeight:"600"}}>
                                {f.status?.toUpperCase()}
                              </span>
                              <span style={{fontSize:"10px",color:"#5a7b9a"}}>{f.created_at ? new Date(f.created_at).toLocaleString() : "-"}</span>
                            </div>
                            <div style={{fontSize:"11px",color:"#8aa4bd",marginBottom:"4px",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{f.url}</div>
                            {f.error_message && <div style={{fontSize:"10px",color:"#ff6666",fontFamily:"monospace"}}>{f.error_message}</div>}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}

            {/* Filter/Sort Bar */}
            <div style={{display:"flex",alignItems:"center",gap:"12px",marginBottom:"20px",flexWrap:"wrap"}}>
              <div style={{position:"relative",flex:"1",minWidth:"200px",maxWidth:"300px"}}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#5a7b9a" strokeWidth="2" style={{position:"absolute",left:"12px",top:"50%",transform:"translateY(-50%)"}}><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
                <input type="text" placeholder="Search posts..." autoComplete="off" spellCheck={false} value={targetDetailSearch}
                  onChange={e=>{setTargetDetailSearch(e.target.value);clearTimeout(targetDetailSearchTimeout.current);targetDetailSearchTimeout.current=setTimeout(()=>setTargetDetailSearchResults(null),300)}}
                  style={{padding:"8px 12px 8px 34px",borderRadius:"3px",border:"1px solid #2a2a2a",width:"100%",background:"#0b1728",color:"#dfe6ed",fontSize:"13px",outline:"none"}}/>
              </div>
              <select value={targetDetailSortBy} onChange={e=>setTargetDetailSortBy(e.target.value)}
                style={{padding:"8px 12px",background:"#161d2f",border:"1px solid #2a2a2a",borderRadius:"3px",color:"#8aa4bd",fontSize:"13px",cursor:"pointer",outline:"none"}}>
                <option value="newest">Newest First</option>
                <option value="oldest">Oldest First</option>
                <option value="title_asc">Title A → Z</option>
                <option value="title_desc">Title Z → A</option>
              </select>
              <select value={targetDetailFilterMediaType} onChange={e=>setTargetDetailFilterMediaType(e.target.value)}
                style={{padding:"8px 12px",background:"#161d2f",border:"1px solid #2a2a2a",borderRadius:"3px",color:"#8aa4bd",fontSize:"13px",cursor:"pointer",outline:"none"}}>
                <option value="all">All Media</option>
                <option value="image">Images</option>
                <option value="video">Videos</option>
                <option value="text">Text</option>
              </select>
              {(targetDetailSearch || targetDetailFilterMediaType !== "all") && (
                <button onClick={()=>{setTargetDetailSearch("");setTargetDetailFilterMediaType("all");setTargetDetailSortBy("newest");setTargetDetailSearchResults(null)}} 
                  style={{padding:"6px 12px",background:"#1c2a3f",border:"1px solid #35c5f444",borderRadius:"3px",color:"#5fd4f8",cursor:"pointer",fontSize:"12px"}}>
                  ✕ Clear
                </button>
              )}
            </div>

            {/* Apply client-side filtering/sorting */}
            {(()=>{
              let filteredPosts = [...targetPosts]
              if(targetDetailSearch){
                const q = targetDetailSearch.toLowerCase()
                filteredPosts = filteredPosts.filter(p => 
                  (p.title||"").toLowerCase().includes(q) || 
                  (p.subreddit||"").toLowerCase().includes(q) ||
                  (p.author||"").toLowerCase().includes(q)
                )
              }
              if(targetDetailFilterMediaType !== "all"){
                filteredPosts = filteredPosts.filter(p => {
                  if(targetDetailFilterMediaType === "image") return !p.is_video && (p.url || p.image_urls?.length > 0)
                  if(targetDetailFilterMediaType === "video") return p.is_video || p.video_url
                  if(targetDetailFilterMediaType === "text") return !p.url && !p.image_urls?.length && !p.is_video && p.selftext
                  return true
                })
              }
              filteredPosts.sort((a,b) => {
                if(targetDetailSortBy === "newest") return new Date(b.created_utc) - new Date(a.created_utc)
                if(targetDetailSortBy === "oldest") return new Date(a.created_utc) - new Date(b.created_utc)
                if(targetDetailSortBy === "title_asc") return (a.title||"").localeCompare(b.title||"")
                if(targetDetailSortBy === "title_desc") return (b.title||"").localeCompare(a.title||"")
                return 0
              })
              return (
              <>
                {filteredPosts.length > 0 && (
                  <div style={{fontSize:"12px",color:"#5a7b9a",marginBottom:"16px",padding:"0 4px"}}>
                    Showing {filteredPosts.length.toLocaleString()} of {targetPosts.length.toLocaleString()} posts
                  </div>
                )}
                <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,minmax(280px,1fr))",gap:"16px"}} className="mobile-grid-2">
                  {filteredPosts.map(p=><PostCard key={p.id} p={p}/>)}
                </div>
                {filteredPosts.length === 0 && targetPosts.length > 0 && (
                  <div style={{padding:"40px",textAlign:"center",color:"#5a7b9a"}}>No posts match the current filters.</div>
                )}
              </>
              )
            })()}
            {targetPostsLoading && (
              <div style={{padding:"40px",textAlign:"center",color:"#35c5f4",fontSize:"14px"}}>
                <span style={{width:"20px",height:"20px",border:"2px solid #333",borderTopColor:"#35c5f4",borderRadius:"50%",display:"inline-block",animation:"spin 1s linear infinite"}}/>
                <style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style>
              </div>
            )}
            {!targetPostsLoading && targetPosts.length === 0 && (
              <div style={{padding:"60px",textAlign:"center",color:"#5a7b9a"}}>No posts found for this target.</div>
            )}
            <div ref={targetPostsLoader} style={{height:"60px"}}/>
          </div>
        )}

      {/* ── WANTED TAB ── */}
      {activeTab === "wanted" && (
        <div style={{padding:"24px",maxWidth:"1400px",margin:"0 auto"}}>
          <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:"24px"}}>
            <div style={{display:"flex",alignItems:"center",gap:"12px"}}>
              <div style={{width:"4px",height:"24px",background:"linear-gradient(180deg,#46d160,#2da64d)",borderRadius:"2px"}} />
              <h2 style={{margin:0,fontSize:"20px",fontWeight:"600"}}>Audit Dashboard</h2>
            </div>
            <button onClick={()=>{loadAuditSummary();loadAuditPosts()}} style={{padding:"8px 16px",background:"#1c2a3f",border:"1px solid #333",borderRadius:"3px",color:"#8aa4bd",cursor:"pointer",fontSize:"13px"}}>↻ Refresh</button>
          </div>
          {auditData && (
            <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(180px,1fr))",gap:"16px",marginBottom:"32px"}}>
              {[
                {label:"Total Posts",value:auditData.total_hidden_posts,color:"#f5f7fa"},
                {label:"Posts All OK",value:auditData.posts_all_ok,color:"#46d160"},
                {label:"Posts w/Issues",value:auditData.posts_with_issues,color:auditData.posts_with_issues>0?"#35c5f4":"#46d160"},
                {label:"Media OK",value:auditData.media_ok,color:"#46d160"},
                {label:"Media Missing",value:auditData.media_missing,color:auditData.media_missing>0?"#35c5f4":"#46d160"},
              ].map(s=>(
                <div key={s.label} style={{background:"linear-gradient(145deg,#1e1e1e,#171717)",padding:"16px",borderRadius:"3px",border:"1px solid #2a2a2a"}}>
                  <div style={{fontSize:"11px",color:"#5a7b9a",marginBottom:"6px",textTransform:"uppercase"}}>{s.label}</div>
                  <div style={{fontSize:"24px",fontWeight:"700",color:s.color,fontVariantNumeric:"tabular-nums"}}>{s.value?.toLocaleString()}</div>
                </div>
              ))}
            </div>
          )}
          <div style={{display:"flex",gap:"12px",marginBottom:"20px"}}>
            <select value={auditFilters.status} onChange={e=>{setAuditFilters(f=>({...f,status:e.target.value}));auditOffsetRef.current=0;setAuditOffset(0);loadAuditPosts(0,e.target.value,auditFilters.subreddit)}}
              style={{padding:"8px 12px",background:"#161d2f",border:"1px solid #333",borderRadius:"3px",color:"#c8d6e0",fontSize:"13px"}}>
              <option value="">All statuses</option><option value="ok">All OK</option><option value="missing">Has Missing</option>
            </select>
            <input type="text" placeholder="r/ subreddit…" autoComplete="off" spellCheck={false} value={auditFilters.subreddit}
              onChange={e=>{setAuditFilters(f=>({...f,subreddit:e.target.value}));auditOffsetRef.current=0;setAuditOffset(0);loadAuditPosts(0,auditFilters.status,e.target.value)}}
              style={{padding:"8px 12px",background:"#161d2f",border:"1px solid #333",borderRadius:"3px",color:"#f5f7fa",fontSize:"13px",width:"140px"}}/>
          </div>
          <div style={{background:"#1c2a3f",borderRadius:"3px",border:"1px solid #2a2a2a",overflow:"hidden"}}>
            <table style={{width:"100%",borderCollapse:"collapse",fontSize:"13px"}}>
              <thead><tr style={{background:"#131b2e"}}>
                {["Status","Subreddit","Title","Media","Date"].map(h=>(
                  <th key={h} style={{padding:"12px 16px",textAlign:"left",color:"#5a7b9a",fontSize:"11px",textTransform:"uppercase"}}>{h}</th>
                ))}
              </tr></thead>
              <tbody>
                {auditPosts.map(p=>(
                  <tr key={p.id} onClick={()=>loadAuditPostDetail(p.id)} style={{cursor:"pointer",borderBottom:"1px solid #222"}} onMouseEnter={e=>e.currentTarget.style.background="#161d2f"} onMouseLeave={e=>e.currentTarget.style.background="transparent"}>
                    <td style={{padding:"12px 16px"}}>
                      {p.status==="ok" && <span style={{background:"#0d2818",color:"#46d160",padding:"3px 8px",borderRadius:"3px",fontSize:"11px"}}>✓ OK</span>}
                      {p.status==="partial" && <span style={{background:"#2d2000",color:"#f9c300",padding:"3px 8px",borderRadius:"3px",fontSize:"11px"}}>⚠ Partial</span>}
                      {p.status==="all_missing" && <span style={{background:"#2d0000",color:"#35c5f4",padding:"3px 8px",borderRadius:"3px",fontSize:"11px"}}>✗ Missing</span>}
                      {p.status==="no_media" && <span style={{background:"#161d2f",color:"#5a7b9a",padding:"3px 8px",borderRadius:"3px",fontSize:"11px"}}>— None</span>}
                    </td>
                    <td style={{padding:"12px 16px",color:"#35c5f4"}}>{p.subreddit}</td>
                    <td style={{padding:"12px 16px",maxWidth:"300px",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{p.title}</td>
                    <td style={{padding:"12px 16px",fontVariantNumeric:"tabular-nums"}}><span style={{color:p.media_missing>0?"#35c5f4":"#46d160"}}>{p.media_ok}</span>/{p.media_count}</td>
                    <td style={{padding:"12px 16px",color:"#5a7b9a"}}>{p.created_utc?new Date(p.created_utc).toLocaleDateString():"-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {auditPosts.length===0 && !auditLoading && <div style={{padding:"30px",textAlign:"center",color:"#5a7b9a"}}>No hidden posts.</div>}
            {auditLoading && <div style={{padding:"30px",textAlign:"center",color:"#5a7b9a"}}>Loading…</div>}
          </div>
          {auditPosts.length > 0 && (
            <div style={{display:"flex",justifyContent:"center",gap:"8px",marginTop:"16px"}}>
              <button onClick={()=>{const o=Math.max(0,auditOffsetRef.current-50);auditOffsetRef.current=o;setAuditOffset(o);loadAuditPosts(o,auditFilters.status,auditFilters.subreddit)}} disabled={auditOffset===0}
                style={{padding:"8px 16px",background:"#1c2a3f",border:"1px solid #333",borderRadius:"3px",color:auditOffset===0?"#3a5068":"#8aa4bd",cursor:auditOffset===0?"not-allowed":"pointer"}}>← Prev</button>
              <button onClick={()=>{const o=auditOffsetRef.current+50;auditOffsetRef.current=o;setAuditOffset(o);loadAuditPosts(o,auditFilters.status,auditFilters.subreddit)}} disabled={auditPosts.length<50}
                style={{padding:"8px 16px",background:"#1c2a3f",border:"1px solid #333",borderRadius:"3px",color:auditPosts.length<50?"#3a5068":"#8aa4bd",cursor:auditPosts.length<50?"not-allowed":"pointer"}}>Next →</button>
            </div>
          )}
        </div>
      )}

      {/* Audit post detail modal */}
      {auditPostDetail && (
        <div style={{position:"fixed",inset:0,background:"rgba(0,0,0,0.9)",display:"flex",alignItems:"center",justifyContent:"center",zIndex:200,padding:"20px"}} onClick={()=>setAuditPostDetail(null)}>
          <div style={{background:"#1a2234",borderRadius:"3px",maxWidth:"600px",width:"100%",maxHeight:"80vh",overflow:"auto",border:"1px solid #222"}} onClick={e=>e.stopPropagation()}>
            <div style={{padding:"20px",borderBottom:"1px solid #1a1a1a"}}>
              <div style={{display:"flex",justifyContent:"space-between",alignItems:"flex-start"}}>
                <div>
                  <div style={{fontSize:"11px",color:"#35c5f4",marginBottom:"4px"}}>r/{auditPostDetail.subreddit}</div>
                  <div style={{fontSize:"16px",fontWeight:"600"}}>{auditPostDetail.title}</div>
                </div>
                {auditPostDetail.overall_status==="ok" && <span style={{background:"#0d2818",color:"#46d160",padding:"4px 10px",borderRadius:"3px",fontSize:"11px"}}>✓ OK</span>}
                {auditPostDetail.overall_status==="partial" && <span style={{background:"#2d2000",color:"#f9c300",padding:"4px 10px",borderRadius:"3px",fontSize:"11px"}}>⚠ Partial</span>}
                {auditPostDetail.overall_status==="all_missing" && <span style={{background:"#2d0000",color:"#35c5f4",padding:"4px 10px",borderRadius:"3px",fontSize:"11px"}}>✗ Missing</span>}
              </div>
            </div>
            <div style={{padding:"20px"}}>
              {auditPostDetail.media.length===0 && <div style={{color:"#5a7b9a"}}>No media items.</div>}
              {auditPostDetail.media.map(m=>(
                <div key={m.id} style={{background:"#131b2e",borderRadius:"3px",padding:"12px",marginBottom:"8px",border:m.resolved_status==="ok"?"1px solid #1a3a1a":"1px solid #3a1a1a"}}>
                  <div style={{marginBottom:"4px"}}>
                    {m.resolved_status==="ok" && <span style={{color:"#46d160",fontSize:"11px"}}>✓ Available</span>}
                    {m.resolved_status==="missing_file" && <span style={{color:"#35c5f4",fontSize:"11px"}}>✗ File Missing</span>}
                    {m.resolved_status==="pending" && <span style={{color:"#7193ff",fontSize:"11px"}}>⏳ Pending</span>}
                    {m.resolved_status==="failed" && <span style={{color:"#35c5f4",fontSize:"11px"}}>✗ Failed</span>}
                  </div>
                  <div style={{fontSize:"12px",color:"#8aa4bd",wordBreak:"break-all"}}>{m.url}</div>
                  {m.file_path && <div style={{fontSize:"11px",color:"#5a7b9a",marginTop:"4px"}}>File: {m.file_exists?"✓":"✗"} | {m.file_path}</div>}
                </div>
              ))}
            </div>
            <div style={{padding:"16px 20px",borderTop:"1px solid #1a1a1a",display:"flex",justifyContent:"flex-end"}}>
              <button onClick={()=>setAuditPostDetail(null)} style={{padding:"8px 16px",background:"#161d2f",border:"1px solid #333",borderRadius:"3px",color:"#8aa4bd"}}>Close</button>
            </div>
          </div>
        </div>
      )}

      {/* ── SYSTEM TAB ── */}
      {activeTab === "system" && (
        <div style={{padding:"24px",maxWidth:"1400px",margin:"0 auto"}}>
          {adminLoading && <div style={{textAlign:"center",padding:"40px",color:"#5a7b9a"}}>Loading…</div>}
          {!adminLoading && !adminData && <div style={{textAlign:"center",padding:"40px",color:"#35c5f4"}}>Failed to load admin data.</div>}
          {adminData && (<>
            <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:"24px",flexWrap:"wrap",gap:"12px"}}>
              <div style={{display:"flex",alignItems:"center",gap:"12px"}}>
                <div style={{width:"4px",height:"24px",background:"linear-gradient(180deg,#35c5f4,#5fd4f8)",borderRadius:"2px"}}/>
                <h2 style={{margin:0,fontSize:"20px",fontWeight:"600"}}>System</h2>
              </div>
              <div style={{display:"flex",alignItems:"center",gap:"8px"}}>
                <button onClick={collapseAllSections} style={{padding:"6px 10px",background:"#1c2a3f",border:"1px solid #333",borderRadius:"3px",color:"#5a7b9a",cursor:"pointer",fontSize:"11px"}}>⊟ All</button>
                <button onClick={expandAllSections} style={{padding:"6px 10px",background:"#1c2a3f",border:"1px solid #333",borderRadius:"3px",color:"#5a7b9a",cursor:"pointer",fontSize:"11px"}}>⊞ All</button>
                {lastUpdated && <span style={{fontSize:"11px",color:"#3a5068",fontVariantNumeric:"tabular-nums"}}>synced {lastUpdated.toLocaleTimeString()}</span>}
                <button onClick={loadAdmin} style={{padding:"8px 16px",background:"#1c2a3f",border:"1px solid #333",borderRadius:"3px",color:"#8aa4bd",cursor:"pointer",fontSize:"13px"}}>↻ Refresh</button>
                <button onClick={()=>{setResetModal(true);setResetInput("");setResetResult(null)}} style={{padding:"8px 16px",background:"#1a0000",border:"1px solid #550000",borderRadius:"3px",color:"#ff4444",cursor:"pointer",fontSize:"13px",fontWeight:"600"}}>⚠ Reset</button>
              </div>
            </div>

            {/* Overview stats */}
            <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(140px,1fr))",gap:"12px",marginBottom:"24px"}}>
              {[
                {label:"Visible",value:adminData.total_posts,color:"#35c5f4"},
                {label:"Hidden",value:adminData.hidden_posts,color:"#8aa4bd"},
                {label:"Comments",value:adminData.total_comments,color:"#7193ff"},
                {label:"Downloaded",value:adminData.downloaded_media,color:"#46d160"},
                {label:"Queued",value:adminData.pending_media,color:"#f9c300"},
                {label:"Total Media",value:adminData.total_media,color:"#f5f7fa"},
              ].map(s=>(
                <div key={s.label} style={{background:"linear-gradient(145deg,#1e1e1e,#171717)",padding:"16px",borderRadius:"3px",border:"1px solid #2a2a2a"}}>
                  <div style={{fontSize:"10px",color:"#5a7b9a",marginBottom:"6px",textTransform:"uppercase",letterSpacing:"0.5px"}}>{s.label}</div>
                  <div style={{fontSize:"26px",fontWeight:"700",color:s.color,fontVariantNumeric:"tabular-nums"}}>{s.value?.toLocaleString()}</div>
                </div>
              ))}
            </div>

            <PostsChart data={adminData.posts_per_day}/>

            {/* Health + Queue */}
            <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,200px)",gap:"12px",marginBottom:"32px"}}>
              <div style={{background:"#1c2a3f",padding:"16px",borderRadius:"3px",border:"1px solid #2a2a2a"}}>
                <div style={{fontSize:"11px",color:"#5a7b9a",marginBottom:"8px"}}>Health</div>
                <div style={{fontSize:"18px",fontWeight:"600",color:healthStatus?.status==="healthy"?"#46d160":healthStatus?.status==="degraded"?"#f9c300":"#35c5f4"}}>{healthStatus?.status||"unknown"}</div>
              </div>
              <div style={{background:"#1c2a3f",padding:"16px",borderRadius:"3px",border:"1px solid #2a2a2a"}}>
                <div style={{fontSize:"11px",color:"#5a7b9a",marginBottom:"8px",display:"flex",justifyContent:"space-between"}}>
                  <span>Queue</span>
                  {queueInfo?.queue_length > 0 && <button onClick={clearQueue} style={{fontSize:"10px",padding:"2px 6px",background:"#2a0000",border:"1px solid #550000",borderRadius:"3px",color:"#ff4444",cursor:"pointer"}}>clear</button>}
                </div>
                <div style={{fontSize:"18px",fontWeight:"600",color:queueInfo?.queue_length>0?"#f9c300":"#f5f7fa"}}>{(queueInfo?.queue_length||0).toLocaleString()}</div>
              </div>
            </div>

            {/* Scrape actions */}
            <div style={{marginBottom:"24px"}}>
              <div style={{display:"flex",alignItems:"center",gap:"12px",marginBottom:"16px"}}>
                <div style={{width:"4px",height:"20px",background:"linear-gradient(180deg,#35c5f4,#5fd4f8)",borderRadius:"2px"}}/>
                <h3 style={{margin:0,fontSize:"16px",fontWeight:"600",color:"#f5f7fa"}}>Actions</h3>
              </div>
              <div style={{display:"flex",gap:"8px",flexWrap:"wrap"}}>
                <div style={{position:"relative",display:"inline-block"}}>
                  <button onClick={()=>{const m=document.getElementById("global-sync-menu");m.style.display=m.style.display==="none"?"block":"none"}} style={{padding:"8px 14px",background:scrapeTriggered?"#46d160":"linear-gradient(135deg,#35c5f4,#5fd4f8)",border:"none",borderRadius:"3px",color:scrapeTriggered?"#000":"#f5f7fa",cursor:"pointer",fontSize:"12px",fontWeight:"600"}}>
                    {scrapeTriggered ? "✓ Fetching…" : "⚡ Fetch New Posts ▼"}
                  </button>
                  <div id="global-sync-menu" style={{display:"none",position:"absolute",top:"100%",left:0,zIndex:100,background:"#1a2234",border:"1px solid #333",borderRadius:"3px",minWidth:"140px",marginTop:"4px",boxShadow:"0 4px 12px rgba(0,0,0,0.4)"}}>
                    <div onClick={()=>{document.getElementById("global-sync-menu").style.display="none";scrapeNow()}} style={{padding:"10px 14px",cursor:"pointer",color:"#f5f7fa",fontSize:"12px",borderBottom:"1px solid #222"}}>Get Latest</div>
                    <div onClick={()=>{document.getElementById("global-sync-menu").style.display="none";triggerBackfill()}} style={{padding:"10px 14px",cursor:"pointer",color:"#7ab3e0",fontSize:"12px"}}>Get History</div>
                  </div>
                </div>
                <button onClick={runArchiveAll} disabled={!!archiveJob} style={{padding:"8px 14px",background:archiveJob?"#243447":"linear-gradient(135deg,#46d160,#2ea84e)",border:"none",borderRadius:"3px",color:archiveJob?"#5a7b9a":"#000",cursor:archiveJob?"not-allowed":"pointer",fontSize:"12px",fontWeight:"700"}}>
                  {archiveJob ? "⏳ Hiding…" : "📦 Hide All Posts"}
                </button>
              </div>
            </div>

            {/* Thumbnail section */}
            <div style={{marginBottom:"24px"}}>
              <div onClick={()=>toggleAdminSection("thumbnails")} style={{display:"flex",alignItems:"center",justifyContent:"space-between",padding:"12px 16px",background:"#1c2a3f",borderRadius:"3px",border:"1px solid #2a2a2a",cursor:"pointer",marginBottom:adminSections.thumbnails?"16px":0}}>
                <div style={{display:"flex",alignItems:"center",gap:"12px"}}>
                  <div style={{width:"4px",height:"20px",background:"linear-gradient(180deg,#7193ff,#5a7ad4)",borderRadius:"2px"}}/>
                  <h3 style={{margin:0,fontSize:"16px",fontWeight:"600",color:"#f5f7fa"}}>Thumbnails</h3>
                </div>
                <span style={{color:"#5a7b9a",fontSize:"14px",transform:adminSections.thumbnails?"rotate(0deg)":"rotate(-90deg)",transition:"transform 0.2s"}}>▼</span>
              </div>
              {adminSections.thumbnails && (
                <div>
                  {thumbStats && (
                    <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(140px,1fr))",gap:"10px",marginBottom:"16px"}}>
                      {[
                        {label:"Media files",value:thumbStats.total_media_with_file,color:"#f5f7fa"},
                        {label:"Thumbs OK",value:thumbStats.with_thumb_in_db,color:"#46d160"},
                        {label:"Missing",value:thumbStats.missing_thumb_in_db,color:thumbStats.missing_thumb_in_db>0?"#f9c300":"#46d160"},
                        {label:"Disk",value:`${thumbStats.thumb_disk_mb} MB`,color:"#8aa4bd"},
                      ].map(s=>(
                        <div key={s.label} style={{background:"#161d2f",padding:"12px 14px",borderRadius:"3px",border:"1px solid #2a2a2a"}}>
                          <div style={{fontSize:"10px",color:"#5a7b9a",marginBottom:"4px",textTransform:"uppercase"}}>{s.label}</div>
                          <div style={{fontSize:"20px",fontWeight:"700",color:s.color,fontVariantNumeric:"tabular-nums"}}>{typeof s.value==="number"?s.value.toLocaleString():s.value}</div>
                        </div>
                      ))}
                    </div>
                  )}
                  <div style={{display:"flex",gap:"10px",flexWrap:"wrap"}}>
                    <button onClick={runThumbBackfill} disabled={!!thumbJob} style={{padding:"10px 18px",background:thumbJob?"#243447":"linear-gradient(135deg,#35c5f4,#5fd4f8)",border:"none",borderRadius:"3px",color:thumbJob?"#5a7b9a":"#f5f7fa",cursor:thumbJob?"not-allowed":"pointer",fontSize:"12px",fontWeight:"600"}}>⬇ Generate Missing</button>
                    <button onClick={runThumbRebuildAll} disabled={!!thumbJob} style={{padding:"10px 18px",background:thumbJob?"#243447":"#1e3a5f",border:"1px solid #2a5a8a",borderRadius:"3px",color:thumbJob?"#5a7b9a":"#7ab3e0",cursor:thumbJob?"not-allowed":"pointer",fontSize:"12px",fontWeight:"600"}}>🔄 Regenerate All</button>
                    <button onClick={runThumbPurgeOrphans} disabled={!!thumbJob} style={{padding:"10px 18px",background:thumbJob?"#243447":"#2a0000",border:"1px solid #550000",borderRadius:"3px",color:thumbJob?"#5a7b9a":"#ff6b6b",cursor:thumbJob?"not-allowed":"pointer",fontSize:"12px",fontWeight:"600"}}>🗑 Delete Orphans</button>
                  </div>
                  {thumbJob && (()=>{
                    const pct = thumbJob.total>0?Math.round(thumbJob.done/thumbJob.total*100):0
                    return <div style={{background:"#161d2f",borderRadius:"3px",border:"1px solid #2a2a2a",padding:"14px",marginTop:"16px"}}>
                      <div style={{display:"flex",justifyContent:"space-between",marginBottom:"8px"}}><span style={{fontSize:"12px",color:"#c8d6e0"}}>{thumbJob.status}</span><span style={{fontSize:"12px",color:"#5a7b9a"}}>{thumbJob.done}/{thumbJob.total}</span></div>
                      <div style={{background:"#131b2e",height:"6px",borderRadius:"3px",overflow:"hidden"}}><div style={{width:`${pct}%`,background:"linear-gradient(90deg,#35c5f4,#5fd4f8)",height:"100%",transition:"width 0.4s"}}/></div>
                    </div>
                  })()}
                </div>
              )}
            </div>

            {/* Media rescan */}
            <div style={{marginBottom:"24px"}}>
              <div onClick={()=>toggleAdminSection("media")} style={{display:"flex",alignItems:"center",justifyContent:"space-between",padding:"12px 16px",background:"#1c2a3f",borderRadius:"3px",border:"1px solid #2a2a2a",cursor:"pointer",marginBottom:adminSections.media?"16px":0}}>
                <div style={{display:"flex",alignItems:"center",gap:"12px"}}>
                  <div style={{width:"4px",height:"20px",background:"linear-gradient(180deg,#35c5f4,#5fd4f8)",borderRadius:"2px"}}/>
                  <h3 style={{margin:0,fontSize:"16px",fontWeight:"600",color:"#f5f7fa"}}>Media Re-scan</h3>
                </div>
                <span style={{color:"#5a7b9a",fontSize:"14px",transform:adminSections.media?"rotate(0deg)":"rotate(-90deg)",transition:"transform 0.2s"}}>▼</span>
              </div>
              {adminSections.media && (
                <div style={{display:"flex",gap:"10px",flexWrap:"wrap"}}>
                  <button onClick={()=>{if(!window.confirm("Scan ALL posts for missing media?"))return;axios.post("/api/admin/media/rescan").then(r=>toastSuccess(`Scanned ${r.data.posts_scanned} posts, queued ${r.data.newly_queued} new`)).catch(err=>toastError("Scan failed"))}}
                    style={{padding:"10px 20px",background:"linear-gradient(135deg,#35c5f4,#5fd4f8)",border:"none",borderRadius:"3px",color:"#f5f7fa",cursor:"pointer",fontSize:"13px",fontWeight:"600"}}>🔍 Scan for Missing</button>
                  <button onClick={()=>{if(!window.confirm("Retry ALL failed downloads?"))return;axios.post("/api/admin/media/rescrape").then(r=>toastSuccess(`Requeued ${r.data.requeued} items`)).catch(err=>toastError("Retry failed"))}}
                    style={{padding:"10px 20px",background:"linear-gradient(135deg,#f9c300,#e6b200)",border:"none",borderRadius:"3px",color:"#000",cursor:"pointer",fontSize:"13px",fontWeight:"600"}}>🔄 Retry Downloads</button>
                </div>
              )}
            </div>

            {/* Database Maintenance */}
            <div style={{marginBottom:"16px"}}>
              <div onClick={()=>toggleAdminSection("database")} style={{display:"flex",alignItems:"center",justifyContent:"space-between",padding:"12px 16px",background:"#1c2a3f",borderRadius:"3px",border:"1px solid #2a2a2a",cursor:"pointer",marginBottom:adminSections.database?"16px":0}}>
                <div style={{display:"flex",alignItems:"center",gap:"12px"}}>
                  <div style={{width:"4px",height:"20px",background:"linear-gradient(180deg,#f9c300,#e6b200)",borderRadius:"2px"}}/>
                  <h3 style={{margin:0,fontSize:"16px",fontWeight:"600",color:"#f5f7fa"}}>Database Maintenance</h3>
                </div>
                <span style={{color:"#5a7b9a",fontSize:"14px",transform:adminSections.database?"rotate(0deg)":"rotate(-90deg)",transition:"transform 0.2s"}}>▼</span>
              </div>
              {adminSections.database && (
                <div>
                  {dbStats && (
                    <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(100px,1fr))",gap:"10px",marginBottom:"16px"}}>
                      {[
                        {label:"Posts",value:dbStats.posts,color:"#f5f7fa"},
                        {label:"Media",value:dbStats.media,color:"#35c5f4"},
                        {label:"Comments",value:dbStats.comments,color:"#7193ff"},
                        {label:"Targets",value:dbStats.targets,color:"#46d160"},
                      ].map(s=>(
                        <div key={s.label} style={{background:"#161d2f",padding:"12px 14px",borderRadius:"3px",border:"1px solid #2a2a2a"}}>
                          <div style={{fontSize:"10px",color:"#5a7b9a",marginBottom:"4px",textTransform:"uppercase"}}>{s.label}</div>
                          <div style={{fontSize:"20px",fontWeight:"700",color:s.color,fontVariantNumeric:"tabular-nums"}}>{s.value?.toLocaleString()}</div>
                        </div>
                      ))}
                    </div>
                  )}
                  {/* Create Full Backup */}
                  <div style={{display:"flex",gap:"10px",flexWrap:"wrap",marginBottom:"16px"}}>
                    <button onClick={()=>createDbBackup("")} disabled={dbBackupLoading} style={{padding:"10px 18px",background:dbBackupLoading?"#243447":"linear-gradient(135deg,#35c5f4,#5fd4f8)",border:"none",borderRadius:"3px",color:dbBackupLoading?"#5a7b9a":"#f5f7fa",cursor:dbBackupLoading?"not-allowed":"pointer",fontSize:"12px",fontWeight:"600"}}>
                      {dbBackupLoading?"Creating...":"💾 Create Full Backup"}
                    </button>
                  </div>

                  {/* Create Partial Backup */}
                  <div style={{background:"#161d2f",borderRadius:"3px",border:"1px solid #2a2a2a",padding:"16px",marginBottom:"16px"}}>
                    <div style={{fontSize:"12px",color:"#5a7b9a",marginBottom:"12px"}}>Create Partial Backup</div>
                    <div style={{display:"flex",gap:"10px",flexWrap:"wrap",marginBottom:"12px"}}>
                      <input type="text" placeholder="label (e.g. askreddit-2024)" 
                        value={partialRestoreFilters.targets} onChange={e=>setPartialRestoreFilters(f=>({...f,targets:e.target.value}))}
                        style={{padding:"8px 12px",background:"#131b2e",border:"1px solid #2a2a2a",borderRadius:"3px",color:"#f5f7fa",fontSize:"12px",outline:"none",width:"180px"}}/>
                      <input type="text" placeholder="subreddits (AskReddit,Programming)" 
                        value={partialRestoreFilters.subreddits} onChange={e=>setPartialRestoreFilters(f=>({...f,subreddits:e.target.value}))}
                        style={{padding:"8px 12px",background:"#131b2e",border:"1px solid #2a2a2a",borderRadius:"3px",color:"#f5f7fa",fontSize:"12px",outline:"none",width:"200px"}}/>
                    </div>
                    <div style={{display:"flex",gap:"10px",flexWrap:"wrap",marginBottom:"12px"}}>
                      <input type="date" title="Before date" 
                        onChange={e=>setPartialRestoreFilters(f=>({...f, before_date: e.target.value}))}
                        style={{padding:"8px 12px",background:"#131b2e",border:"1px solid #2a2a2a",borderRadius:"3px",color:"#f5f7fa",fontSize:"12px",outline:"none"}}/>
                      <input type="date" title="After date"
                        onChange={e=>setPartialRestoreFilters(f=>({...f, after_date: e.target.value}))}
                        style={{padding:"8px 12px",background:"#131b2e",border:"1px solid #2a2a2a",borderRadius:"3px",color:"#f5f7fa",fontSize:"12px",outline:"none"}}/>
                    </div>
                    <button onClick={()=>createPartialBackup(partialRestoreFilters.targets || "partial", partialRestoreFilters, "posts,comments,media")} disabled={dbBackupLoading} 
                      style={{padding:"8px 16px",background:dbBackupLoading?"#243447":"#1e3a5f",border:"1px solid #2a5a8a",borderRadius:"3px",color:"#7ab3e0",cursor:dbBackupLoading?"not-allowed":"pointer",fontSize:"12px",fontWeight:"600"}}>
                      {dbBackupLoading?"Creating...":"📦 Create Partial"}
                    </button>
                  </div>

                  {/* Merge Backups */}
                  <div style={{background:"#161d2f",borderRadius:"3px",border:"1px solid #2a2a2a",padding:"16px",marginBottom:"16px"}}>
                    <div style={{fontSize:"12px",color:"#5a7b9a",marginBottom:"12px"}}>Merge Partial Backups</div>
                    <div style={{display:"flex",gap:"10px",flexWrap:"wrap",marginBottom:"12px"}}>
                      <input type="text" placeholder="source files (comma sep)" value={mergeBackupsState.sources} onChange={e=>setMergeBackupsState(s=>({...s, sources: e.target.value}))}
                        style={{padding:"8px 12px",background:"#131b2e",border:"1px solid #2a2a2a",borderRadius:"3px",color:"#f5f7fa",fontSize:"12px",outline:"none",width:"260px"}}/>
                      <input type="text" placeholder="output name" value={mergeBackupsState.output} onChange={e=>setMergeBackupsState(s=>({...s, output: e.target.value}))}
                        style={{padding:"8px 12px",background:"#131b2e",border:"1px solid #2a2a2a",borderRadius:"3px",color:"#f5f7fa",fontSize:"12px",outline:"none",width:"140px"}}/>
                    </div>
                    <div style={{display:"flex",gap:"10px"}}>
                      <button onClick={()=>runMergeBackups("preview")} disabled={mergeBackupsState.loading} style={{padding:"8px 16px",background:mergeBackupsState.loading?"#243447":"#1e3a5f",border:"1px solid #2a5a8a",borderRadius:"3px",color:"#7ab3e0",cursor:mergeBackupsState.loading?"not-allowed":"pointer",fontSize:"12px",fontWeight:"600"}}>Preview</button>
                      <button onClick={()=>runMergeBackups("merge")} disabled={mergeBackupsState.loading} style={{padding:"8px 16px",background:mergeBackupsState.loading?"#243447":"#2a0000",border:"1px solid #550000",borderRadius:"3px",color:mergeBackupsState.loading?"#5a7b9a":"#ff6b6b",cursor:mergeBackupsState.loading?"not-allowed":"pointer",fontSize:"12px",fontWeight:"600"}}>Merge</button>
                    </div>
                    {mergeBackupsState.result && (
                      <div style={{background:"#0a1a0a",borderRadius:"3px",border:"1px solid #1a4a1a",padding:"12px",marginTop:"12px",fontSize:"12px",color:"#46d160"}}>
                        {mergeBackupsState.result.status === "preview" ? <>Would merge: {mergeBackupsState.result.would_merge?.posts} posts</> : <>Merged: {mergeBackupsState.result.counts?.posts} posts</>}
                      </div>
                    )}
                  </div>

                  {/* Available Backups */}
                  {dbBackups.length > 0 && (
                    <div style={{marginBottom:"16px"}}>
                      <div style={{fontSize:"11px",color:"#5a7b9a",marginBottom:"8px"}}>Available Backups ({dbBackups.length})</div>
                      <div style={{display:"flex",flexWrap:"wrap",gap:"8px"}}>
                        {dbBackups.map(b=>(
                          <div key={b.name} style={{background:"#161d2f",borderRadius:"3px",border:"1px solid #2a2a2a",padding:"8px 12px",display:"flex",alignItems:"center",gap:"8px"}}>
                            <span style={{fontSize:"12px",color:"#8aa4bd"}}>{b.name}</span>
                            <span style={{fontSize:"10px",color:"#5a7b9a"}}>{(b.size/1024).toFixed(1)}KB</span>
                            <button onClick={()=>getBackupInfo(b.name)} style={{padding:"4px 8px",background:"#1c2a3f",border:"1px solid #333",borderRadius:"3px",color:"#8aa4bd",cursor:"pointer",fontSize:"10px"}}>ℹ</button>
                            <button onClick={()=>deleteBackup(b.name)} style={{padding:"4px 8px",background:"#2a0000",border:"1px solid #550000",borderRadius:"3px",color:"#ff6b6b",cursor:"pointer",fontSize:"10px"}}>✕</button>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Restore from Backup */}
                  <div style={{background:"#161d2f",borderRadius:"3px",border:"1px solid #2a2a2a",padding:"16px",marginBottom:"16px"}}>
                    <div style={{fontSize:"12px",color:"#5a7b9a",marginBottom:"12px"}}>Restore from Backup</div>
                    <div style={{marginBottom:"12px"}}>
                      <select value={partialRestoreBackup} onChange={e=>setPartialRestoreBackup(e.target.value)}
                        style={{padding:"8px 12px",background:"#131b2e",border:"1px solid #2a2a2a",borderRadius:"3px",color:"#f5f7fa",fontSize:"12px",outline:"none",width:"280px"}}>
                        <option value="">Select backup...</option>
                        {dbBackups.map(b=>(<option key={b.name} value={b.name}>{b.name}</option>))}
                      </select>
                    </div>
                    <div style={{display:"flex",gap:"10px",flexWrap:"wrap",marginBottom:"12px"}}>
                      <input type="text" placeholder="subreddits filter" value={partialRestoreFilters.subreddits} onChange={e=>setPartialRestoreFilters(f=>({...f,subreddits:e.target.value}))}
                        style={{padding:"8px 12px",background:"#131b2e",border:"1px solid #2a2a2a",borderRadius:"3px",color:"#f5f7fa",fontSize:"12px",outline:"none",width:"180px"}}/>
                      <input type="text" placeholder="targets filter" value={partialRestoreFilters.targets} onChange={e=>setPartialRestoreFilters(f=>({...f,targets:e.target.value}))}
                        style={{padding:"8px 12px",background:"#131b2e",border:"1px solid #2a2a2a",borderRadius:"3px",color:"#f5f7fa",fontSize:"12px",outline:"none",width:"160px"}}/>
                    </div>
                    <div style={{display:"flex",gap:"10px",flexWrap:"wrap",marginBottom:"12px"}}>
                      <input type="date" onChange={e=>setPartialRestoreFilters(f=>({...f,before_date:e.target.value}))}
                        style={{padding:"8px 12px",background:"#131b2e",border:"1px solid #2a2a2a",borderRadius:"3px",color:"#f5f7fa",fontSize:"12px",outline:"none"}}/>
                      <input type="date" onChange={e=>setPartialRestoreFilters(f=>({...f,after_date:e.target.value}))}
                        style={{padding:"8px 12px",background:"#131b2e",border:"1px solid #2a2a2a",borderRadius:"3px",color:"#f5f7fa",fontSize:"12px",outline:"none"}}/>
                    </div>
                    <div style={{display:"flex",gap:"10px"}}>
                      <button onClick={()=>runPartialRestore("preview")} disabled={partialRestoreLoading} style={{padding:"8px 16px",background:partialRestoreLoading?"#243447":"#1e3a5f",border:"1px solid #2a5a8a",borderRadius:"3px",color:"#7ab3e0",cursor:partialRestoreLoading?"not-allowed":"pointer",fontSize:"12px",fontWeight:"600"}}>Preview</button>
                      <button onClick={()=>runPartialRestore("restore")} disabled={partialRestoreLoading} style={{padding:"8px 16px",background:partialRestoreLoading?"#243447":"#2a0000",border:"1px solid #550000",borderRadius:"3px",color:partialRestoreLoading?"#5a7b9a":"#ff6b6b",cursor:partialRestoreLoading?"not-allowed":"pointer",fontSize:"12px",fontWeight:"600"}}>Restore</button>
                    </div>
                    {partialRestoreResult && (
                      <div style={{background:"#0a1a0a",borderRadius:"3px",border:"1px solid #1a4a1a",padding:"12px",marginTop:"12px",fontSize:"12px",color:"#46d160"}}>
                        {partialRestoreResult.status === "preview" ? <>Would restore: {partialRestoreResult.would_restore?.posts} posts</> : <>Restored: {partialRestoreResult.restored?.posts} posts</>}
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>

            {/* Activity log */}
            <div style={{marginBottom:"16px"}}>
              <div onClick={()=>toggleAdminSection("activity")} style={{display:"flex",alignItems:"center",justifyContent:"space-between",padding:"12px 16px",background:"#1c2a3f",borderRadius:"3px",border:"1px solid #2a2a2a",cursor:"pointer",marginBottom:adminSections.activity?"16px":0}}>
                <div style={{display:"flex",alignItems:"center",gap:"12px"}}>
                  <div style={{width:"4px",height:"20px",background:"linear-gradient(180deg,#46d160,#2ea84e)",borderRadius:"2px"}}/>
                  <h3 style={{margin:0,fontSize:"16px",fontWeight:"600",color:"#f5f7fa"}}>Recent Activity</h3>
                  <div style={{width:"6px",height:"6px",borderRadius:"50%",background:liveConnected?"#46d160":"#3a5068",boxShadow:liveConnected?"0 0 6px #46d160":"none"}}/>
                </div>
                <span style={{color:"#5a7b9a",fontSize:"14px",transform:adminSections.activity?"rotate(0deg)":"rotate(-90deg)",transition:"transform 0.2s"}}>▼</span>
              </div>
              {adminSections.activity && (
                <div style={{background:"linear-gradient(145deg,#1e1e1e,#171717)",borderRadius:"3px",border:"1px solid #2a2a2a",overflow:"hidden"}}>
                  <table style={{width:"100%",borderCollapse:"collapse",fontSize:"13px"}}>
                    <thead><tr style={{background:"#131b2e",borderBottom:"1px solid #2a2a2a"}}>
                      {["Time","Subreddit","Author","Title"].map(h=>(
                        <th key={h} style={{padding:"12px 14px",textAlign:"left",color:"#5a7b9a",fontWeight:"500",fontSize:"11px",textTransform:"uppercase"}}>{h}</th>
                      ))}
                    </tr></thead>
                    <tbody>
                      <style>{`@keyframes rowFlash{0%{background:#1c2e00}60%{background:#111c00}100%{background:transparent}}.row-new{animation:rowFlash 4s ease-out forwards}`}</style>
                      {logs && logs.map(l=>(
                        <tr key={l.id} className={highlightedRows.has(l.id)?"row-new":""} style={{borderBottom:"1px solid #222"}}>
                          <td style={{padding:"12px 14px",color:"#5a7b9a"}}>{l.created_utc?new Date(l.created_utc).toLocaleTimeString():"-"}</td>
                          <td style={{padding:"12px 14px"}}><span style={{background:"rgba(53,197,244,0.13)",color:"#35c5f4",padding:"4px 8px",borderRadius:"3px",fontSize:"12px",fontWeight:"500"}}>{l.subreddit||"-"}</span></td>
                          <td style={{padding:"12px 14px",color:"#8aa4bd"}}>{l.author||"-"}</td>
                          <td style={{padding:"12px 14px",maxWidth:"400px",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",color:"#c8d6e0"}}>{l.title||"-"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </>)}
        </div>
      )}

      {/* ── ACTIVITY TAB ── */}
      {activeTab === "activity" && (
        <div style={{padding:"24px",maxWidth:"1400px",margin:"0 auto"}}>
          <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:"24px",flexWrap:"wrap",gap:"12px"}}>
            <div style={{display:"flex",alignItems:"center",gap:"12px"}}>
              <div style={{width:"4px",height:"24px",background:"linear-gradient(180deg,#46d160,#2ea84e)",borderRadius:"2px"}}/>
              <h2 style={{margin:0,fontSize:"20px",fontWeight:"600"}}>Activity Stream</h2>
              <div style={{width:"6px",height:"6px",borderRadius:"50%",background:liveConnected?"#46d160":"#3a5068",boxShadow:liveConnected?"0 0 6px #46d160":"none"}}/>
            </div>
            <span style={{fontSize:"11px",color:"#3a5068",fontVariantNumeric:"tabular-nums"}}>synced {lastUpdated?.toLocaleTimeString()}</span>
          </div>
          <div style={{background:"linear-gradient(145deg,#1e1e1e,#171717)",borderRadius:"3px",border:"1px solid #2a2a2a",overflow:"hidden"}}>
            <table style={{width:"100%",borderCollapse:"collapse",fontSize:"13px"}}>
              <thead><tr style={{background:"#131b2e",borderBottom:"1px solid #2a2a2a"}}>
                {["Time","Type","Subreddit","Author","Title"].map(h=>(
                  <th key={h} style={{padding:"12px 14px",textAlign:"left",color:"#5a7b9a",fontWeight:"500",fontSize:"11px",textTransform:"uppercase"}}>{h}</th>
                ))}
              </tr></thead>
              <tbody>
                <style>{`@keyframes rowFlash{0%{background:#1c2e00}60%{background:#111c00}100%{background:transparent}}.row-new{animation:rowFlash 4s ease-out forwards}.row-failure{background:#2a1a1a}.row-failure:hover{background:#3a2a2a}`}</style>
                {logs && logs.map(l=>(
                  <tr key={`${l.type}-${l.id}`} className={l.type === "failure" ? "row-failure" : (highlightedRows.has(l.id) ? "row-new" : "")} style={{borderBottom:"1px solid #222"}}>
                    <td style={{padding:"12px 14px",color:"#5a7b9a",fontSize:"12px"}}>
                      {l.type === "failure" ? (l.created_at ? new Date(l.created_at).toLocaleTimeString() : "-") : (l.created_utc ? new Date(l.created_utc).toLocaleTimeString() : "-")}
                    </td>
                    <td style={{padding:"12px 14px"}}>
                      {l.type === "failure" ? (
                        <span style={{background:l.status==="failed"?"#3a1a1a":"#3a2a1a",color:l.status==="failed"?"#ff6666":"#ffaa00",padding:"2px 8px",borderRadius:"3px",fontSize:"10px",fontWeight:"600"}}>
                          {l.status?.toUpperCase()}
                        </span>
                      ) : (
                        <span style={{background:"rgba(53,197,244,0.13)",color:"#35c5f4",padding:"4px 8px",borderRadius:"3px",fontSize:"10px",fontWeight:"500"}}>POST</span>
                      )}
                    </td>
                    <td style={{padding:"12px 14px"}}><span style={{background:"rgba(53,197,244,0.13)",color:"#35c5f4",padding:"4px 8px",borderRadius:"3px",fontSize:"12px",fontWeight:"500"}}>{l.subreddit||"-"}</span></td>
                    <td style={{padding:"12px 14px",color:"#8aa4bd"}}>{l.author||"-"}</td>
                    <td style={{padding:"12px 14px",maxWidth:"400px",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",color:l.type==="failure"?"#ff6666":"#c8d6e0"}}>
                      {l.type === "failure" ? (l.post_title || l.url) : l.title}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── LOGS TAB ── */}
      {activeTab === "logs" && role === "admin" && (() => {
        const [logFiles, setLogFiles] = useState([])
        const [selectedLog, setSelectedLog] = useState("api")
        const [logs, setLogs] = useState([])
        const [autoRefresh, setAutoRefresh] = useState(false)
        const logsInterval = useRef(null)

        const logNames = ["api", "db", "redis", "ingester", "downloader", "grafana", "prometheus"]

        const loadLogs = useCallback(() => {
          fetch(`/logs/${selectedLog}.log`).then(r => r.text()).then(t => {
            setLogs(t.split("\n").filter(Boolean).slice(-200))
          }).catch(() => setLogs([]))
        }, [selectedLog])

        useEffect(() => {
          setLogFiles(logNames)
        }, [])

        useEffect(() => {
          loadLogs()
        }, [loadLogs])

        useEffect(() => {
          if (autoRefresh) {
            logsInterval.current = setInterval(loadLogs, 3000)
          } else if (logsInterval.current) {
            clearInterval(logsInterval.current)
          }
          return () => { if (logsInterval.current) clearInterval(logsInterval.current) }
        }, [autoRefresh, loadLogs])

        const containerColors = {
          api: "#35c5f4", db: "#ff6666", redis: "#a855f7",
          ingester: "#46d160", downloader: "#f9c300", grafana: "#e74c3c", prometheus: "#e67e22"
        }

        return (
          <div style={{padding:"24px",maxWidth:"1400px",margin:"0 auto"}}>
            <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:"24px",flexWrap:"wrap",gap:"12px"}}>
              <div style={{display:"flex",alignItems:"center",gap:"12px"}}>
                <div style={{width:"4px",height:"24px",background:"linear-gradient(180deg,#a855f7,#9333ea)",borderRadius:"2px"}}/>
                <h2 style={{margin:0,fontSize:"20px",fontWeight:"600"}}>Container Logs</h2>
                <div style={{width:"6px",height:"6px",borderRadius:"50%",background:autoRefresh?"#46d160":"#3a5068",boxShadow:autoRefresh?"0 0 6px #46d160":"none"}}/>
              </div>
            </div>

            <div style={{display:"flex",gap:"12px",marginBottom:"20px",flexWrap:"wrap",alignItems:"center"}}>
              <select value={selectedLog} onChange={e => setSelectedLog(e.target.value)}
                style={{padding:"8px 12px",background:"#161d2f",border:"1px solid #333",borderRadius:"3px",color:"#c8d6e0",fontSize:"13px",minWidth:"160px"}}>
                {logFiles.map(name => (
                  <option key={name} value={name}>{name}</option>
                ))}
              </select>
              <button onClick={loadLogs} style={{padding:"8px 16px",background:"#1c2a3f",border:"1px solid #333",borderRadius:"3px",color:"#8aa4bd",cursor:"pointer",fontSize:"13px"}}>↻ Refresh</button>
              <button onClick={() => setAutoRefresh(!autoRefresh)} style={{padding:"8px 16px",background:autoRefresh?"#46d160":"#1c2a3f",border:"1px solid #333",borderRadius:"3px",color:autoRefresh?"#000":"#8aa4bd",cursor:"pointer",fontSize:"13px"}}>
                {autoRefresh ? "⏸ Live" : "▶ Live"}
              </button>
            </div>

            <div style={{background:"#0d0d0d",borderRadius:"3px",border:"1px solid #2a2a2a",overflow:"hidden",maxHeight:"calc(100vh - 250px)",overflowY:"auto"}}>
              <div style={{padding:"12px",fontFamily:"'SF Mono',Monaco,'Courier New',monospace",fontSize:"11px",lineHeight:"1.6"}}>
                {logs.length === 0 ? (
                  <div style={{color:"#5a7b9a",padding:"20px",textAlign:"center"}}>No logs available</div>
                ) : (
                  logs.map((line, idx) => {
                    const color = containerColors[selectedLog] || "#8aa4bd"
                    const tsMatch = line.match(/^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[^\s]*)\s*(.*)/)
                    const ts = tsMatch ? tsMatch[1] : null
                    const msg = tsMatch ? tsMatch[2] : line
                    return (
                      <div key={idx} style={{display:"flex",gap:"12px",padding:"2px 0",borderBottom:"1px solid #1a1a1a"}}>
                        <span style={{color:"#3a5068",minWidth:"160px",fontVariantNumeric:"tabular-nums",flexShrink:0}}>{ts || "-"}</span>
                        <span style={{color,minWidth:"80px",fontWeight:"500",flexShrink:0}}>{selectedLog}</span>
                        <span style={{color:"#c8d6e0",wordBreak:"break-all"}}>{msg}</span>
                      </div>
                    )
                  })
                )}
              </div>
            </div>
          </div>
        )
      })()}

      {/* ── LIBRARY TAB ── */}
      {activeTab === "library" && (<>
        {newPostsAvailable > 0 && !searchResults && (
          <button onClick={refreshPosts} style={{position:"sticky",top:"73px",zIndex:90,width:"100%",margin:"0",padding:"12px 24px",background:"linear-gradient(135deg,#35c5f4,#5fd4f8)",color:"#f5f7fa",textAlign:"center",cursor:"pointer",fontSize:"14px",fontWeight:"600",boxShadow:"0 4px 20px rgba(255,69,0,0.4)",border:"none"}}>
            ↑ {newPostsAvailable} new post{newPostsAvailable>1?"s":""} — click to refresh
          </button>
        )}
        {!searchResults && (
          <div style={{borderBottom:"1px solid #1e1e1e",background:"#0f1829"}}>
            <div style={{padding:"8px 16px",display:"flex",alignItems:"center",justifyContent:"space-between",maxWidth:"1400px",margin:"0 auto"}}>
              <div style={{display:"flex",alignItems:"center",gap:"8px"}}>
                <button onClick={()=>setFilterBarOpen(o=>!o)} style={{display:"flex",alignItems:"center",gap:"6px",padding:"8px 14px",background:filterBarOpen||hasActiveFilters()?"#35c5f418":"#161d2f",border:`1px solid ${filterBarOpen||hasActiveFilters()?"rgba(53,197,244,0.27)":"#243447"}`,borderRadius:"3px",color:hasActiveFilters()?"#5fd4f8":"#8aa4bd",cursor:"pointer",fontSize:"13px",fontWeight:"500"}}>
                  ⚙ Filters {hasActiveFilters() && <span style={{background:"#35c5f4",color:"#f5f7fa",borderRadius:"3px",padding:"1px 6px",fontSize:"10px",fontWeight:"700"}}>ON</span>}
                  <span style={{fontSize:"10px",opacity:0.6}}>{filterBarOpen?"▲":"▼"}</span>
                </button>
                <select value={sortBy} onChange={e=>{const v=e.target.value;setSortBy(v);applyFilters({...filtersRef.current,sort:v})}}
                  style={{padding:"8px 12px",background:"#161d2f",border:"1px solid #2a2a2a",borderRadius:"3px",color:sortBy!=="last_added"?"#5fd4f8":"#8aa4bd",fontSize:"13px",cursor:"pointer",outline:"none"}}>
                  <option value="last_added">Last added</option><option value="newest">Reddit date ↓</option><option value="oldest">Reddit date ↑</option><option value="title_asc">Title A → Z</option><option value="title_desc">Title Z → A</option>
                </select>
              </div>
              {hasActiveFilters() && <button onClick={clearFilters} style={{padding:"8px 12px",background:"#1c2a3f",border:"1px solid #35c5f444",borderRadius:"3px",color:"#5fd4f8",cursor:"pointer",fontSize:"12px",fontWeight:"500"}}>✕ Clear</button>}
            </div>
            {filterBarOpen && (
              <div style={{padding:"12px 16px 16px",borderTop:"1px solid #1a1a1a"}}>
                <div style={{maxWidth:"1400px",margin:"0 auto",display:"flex",alignItems:"center",gap:"10px",flexWrap:"wrap"}}>
                  <input type="text" placeholder="r/ subreddit…" autoComplete="off" spellCheck={false} value={filterSubreddit}
                    onChange={e=>{const v=e.target.value;setFilterSubreddit(v);clearTimeout(searchTimeout.current);searchTimeout.current=setTimeout(()=>applyFilters({...filtersRef.current,subreddit:v}),400)}}
                    style={{padding:"9px 12px",background:"#161d2f",border:"1px solid #2a2a2a",borderRadius:"3px",color:"#f5f7fa",fontSize:"13px",outline:"none",width:"140px"}}/>
                  <input type="text" placeholder="u/ author…" autoComplete="off" spellCheck={false} value={filterAuthor}
                    onChange={e=>{const v=e.target.value;setFilterAuthor(v);clearTimeout(searchTimeout.current);searchTimeout.current=setTimeout(()=>applyFilters({...filtersRef.current,author:v}),400)}}
                    style={{padding:"9px 12px",background:"#161d2f",border:"1px solid #2a2a2a",borderRadius:"3px",color:"#f5f7fa",fontSize:"13px",outline:"none",width:"140px"}}/>
                  <div style={{display:"flex",alignItems:"center",gap:"6px",flexWrap:"wrap"}}>
                    {[{value:"image",label:"🖼 Images"},{value:"video",label:"🎬 Videos"},{value:"text",label:"📝 Text"}].map(mt=>(
                      <label key={mt.value} style={{display:"flex",alignItems:"center",gap:"5px",cursor:"pointer",padding:"7px 10px",background:filterMediaTypes.includes(mt.value)?"rgba(53,197,244,0.13)":"#161d2f",borderRadius:"3px",border:"1px solid",borderColor:filterMediaTypes.includes(mt.value)?"#35c5f4":"#243447",minHeight:"36px"}}>
                        <input type="checkbox" checked={filterMediaTypes.includes(mt.value)} onChange={e=>{
                          const newTypes=e.target.checked?[...filterMediaTypes,mt.value]:filterMediaTypes.filter(t=>t!==mt.value)
                          setFilterMediaTypes(newTypes);applyFilters({...filtersRef.current,mediaTypes:newTypes})
                        }} style={{width:"14px",height:"14px",accentColor:"#35c5f4"}}/>
                        <span style={{fontSize:"12px",color:filterMediaTypes.includes(mt.value)?"#5fd4f8":"#5a7b9a"}}>{mt.label}</span>
                      </label>
                    ))}
                  </div>
                  <label style={{display:"flex",alignItems:"center",gap:"6px",cursor:"pointer",minHeight:"36px"}}>
                    <input type="checkbox" checked={showNsfw} onChange={e=>{const v=e.target.checked;setShowNsfw(v);localStorage.setItem("showNsfw",String(v));applyFilters({...filtersRef.current,nsfw:v})}} style={{width:"16px",height:"16px",accentColor:"#35c5f4"}}/>
                    <span style={{fontSize:"12px",color:showNsfw?"#5fd4f8":"#5a7b9a",textTransform:"uppercase"}}>NSFW</span>
                  </label>
                </div>
              </div>
            )}
          </div>
        )}
        {searchResults && (
          <div style={{padding:"24px",maxWidth:"1400px",margin:"0 auto"}}>
            <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:"24px"}}>
              <h2 style={{margin:0,fontSize:"20px",fontWeight:"600"}}>Search Results ({searchResults.length})</h2>
              <button onClick={()=>{setSearchResults(null);setSearch("")}} style={{padding:"10px 20px",background:"#1c2a3f",border:"1px solid #333",borderRadius:"3px",color:"#f5f7fa",cursor:"pointer",fontSize:"14px"}}>Clear</button>
            </div>
            <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,minmax(240px,1fr))",gap:"16px"}}>
              {searchResults.map(p=>(
                <div key={p.id} onClick={()=>{setGalleryIdx(0);setSelectedPost(p)}} className="post-card" style={{background:"linear-gradient(145deg,#1e1e1e,#171717)",borderRadius:"3px",overflow:"hidden",cursor:"pointer",border:"1px solid #2a2a2a"}}>
                  <div style={{padding:"16px"}}>
                    <div style={{fontSize:"11px",color:"#35c5f4",textTransform:"uppercase",letterSpacing:"1px",fontWeight:"600",marginBottom:"6px"}}>{p.subreddit?`r/${p.subreddit}`:""}</div>
                    <div style={{fontWeight:"500",marginBottom:"8px",lineHeight:"1.4",color:"#dfe6ed"}}>{p.title}</div>
                    {p.author && <div style={{fontSize:"12px",color:"#5a7b9a"}}>u/{p.author}</div>}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
        {!searchResults && (
          <div style={{padding:"24px",maxWidth:"1400px",margin:"0 auto"}}>
            <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,minmax(280px,1fr))",gap:"16px"}} className="mobile-grid-2">
              {posts.map(p=><PostCard key={p.id} p={p}/>)}
            </div>
            {isLoading && <div style={{padding:"40px",textAlign:"center",color:"#35c5f4"}}><span style={{width:"20px",height:"20px",border:"2px solid #333",borderTopColor:"#35c5f4",borderRadius:"50%",display:"inline-block",animation:"spin 1s linear infinite"}}/></div>}
            {!isLoading && posts.length===0 && <div style={{padding:"60px",textAlign:"center",color:"#5a7b9a"}}>No posts found.</div>}
            <div ref={loader} style={{padding:"60px",textAlign:"center",color:"#3a5068"}}>
              <span style={{width:"20px",height:"20px",border:"2px solid #333",borderTopColor:"#35c5f4",borderRadius:"50%",display:"inline-block",animation:"spin 1s linear infinite"}}/>
              <style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style>
            </div>
          </div>
        )}
      </>)}

      {/* ── HIDDEN TAB ── */}
      {activeTab === "archive" && (<>
        {!archiveSearchResults && (
          <div style={{borderBottom:"1px solid #1e1e1e",background:"#0f1829"}}>
            <div style={{padding:"8px 16px",display:"flex",alignItems:"center",justifyContent:"space-between",gap:"8px",flexWrap:"wrap",maxWidth:"1400px",margin:"0 auto"}}>
              <div style={{display:"flex",alignItems:"center",gap:"8px",flexWrap:"wrap"}}>
                <button onClick={()=>setArchiveFilterBarOpen(o=>!o)} style={{display:"flex",alignItems:"center",gap:"6px",padding:"8px 14px",background:archiveFilterBarOpen||hasActiveArchiveFilters()?"#35c5f418":"#161d2f",border:`1px solid ${archiveFilterBarOpen||hasActiveArchiveFilters()?"rgba(53,197,244,0.27)":"#243447"}`,borderRadius:"3px",color:hasActiveArchiveFilters()?"#5fd4f8":"#8aa4bd",cursor:"pointer",fontSize:"13px",fontWeight:"500"}}>
                  👁 Filters {hasActiveArchiveFilters() && <span style={{background:"#35c5f4",color:"#f5f7fa",borderRadius:"3px",padding:"1px 6px",fontSize:"10px",fontWeight:"700"}}>ON</span>}
                  <span style={{fontSize:"10px",opacity:0.6}}>{archiveFilterBarOpen?"▲":"▼"}</span>
                </button>
                <select value={archiveSortBy} onChange={e=>{const v=e.target.value;setArchiveSortBy(v);applyArchiveFilters({...archiveFiltersRef.current,sort:v})}}
                  style={{padding:"8px 12px",background:"#161d2f",border:"1px solid #2a2a2a",borderRadius:"3px",color:"#8aa4bd",fontSize:"13px",cursor:"pointer",outline:"none"}}>
                  <option value="last_added">Last added</option><option value="newest">Reddit date ↓</option><option value="oldest">Reddit date ↑</option><option value="title_asc">Title A → Z</option><option value="title_desc">Title Z → A</option>
                </select>
              </div>
              <div style={{display:"flex",alignItems:"center",gap:"8px"}}>
                {hasActiveArchiveFilters() && <button onClick={clearArchiveFilters} style={{padding:"8px 12px",background:"#1c2a3f",border:"1px solid #35c5f444",borderRadius:"3px",color:"#5fd4f8",cursor:"pointer",fontSize:"12px",fontWeight:"500"}}>✕ Clear</button>}
                <div style={{position:"relative"}}>
                  <span style={{position:"absolute",left:"12px",top:"50%",transform:"translateY(-50%)",color:"#5a7b9a",fontSize:"15px"}}>⌕</span>
                  <input type="search" placeholder="Search hidden…" autoComplete="off" spellCheck={false} value={archiveSearch} onChange={handleArchiveSearch}
                    style={{padding:"8px 12px 8px 36px",borderRadius:"3px",border:"1px solid #333",width:"200px",background:"#161d2f",color:"#f5f7fa",fontSize:"13px",outline:"none"}}/>
                </div>
              </div>
            </div>
            {archiveFilterBarOpen && (
              <div style={{padding:"12px 16px 16px",borderTop:"1px solid #1a1a1a"}}>
                <div style={{maxWidth:"1400px",margin:"0 auto",display:"flex",alignItems:"center",gap:"10px",flexWrap:"wrap"}}>
                  <input type="text" placeholder="r/ subreddit…" autoComplete="off" spellCheck={false} value={archiveFilterSubreddit}
                    onChange={e=>{const v=e.target.value;setArchiveFilterSubreddit(v);clearTimeout(archiveSearchTimeout.current);archiveSearchTimeout.current=setTimeout(()=>applyArchiveFilters({...archiveFiltersRef.current,subreddit:v}),400)}}
                    style={{padding:"9px 12px",background:"#161d2f",border:"1px solid #2a2a2a",borderRadius:"3px",color:"#f5f7fa",fontSize:"13px",outline:"none",width:"140px"}}/>
                  <input type="text" placeholder="u/ author…" autoComplete="off" spellCheck={false} value={archiveFilterAuthor}
                    onChange={e=>{const v=e.target.value;setArchiveFilterAuthor(v);clearTimeout(archiveSearchTimeout.current);archiveSearchTimeout.current=setTimeout(()=>applyArchiveFilters({...archiveFiltersRef.current,author:v}),400)}}
                    style={{padding:"9px 12px",background:"#161d2f",border:"1px solid #2a2a2a",borderRadius:"3px",color:"#f5f7fa",fontSize:"13px",outline:"none",width:"140px"}}/>
                </div>
              </div>
            )}
          </div>
        )}
        <div style={{padding:"24px",maxWidth:"1400px",margin:"0 auto"}}>
          {!archiveSearchResults && (
            <>
              {archivePosts.length===0 && !archiveIsLoading && <div style={{padding:"60px",textAlign:"center",color:"#5a7b9a"}}>No hidden posts yet.</div>}
              <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,minmax(240px,1fr))",gap:"16px"}}>
                {archivePosts.map(p=><PostCard key={p.id} p={p} isArchive/>)}
              </div>
              {archiveIsLoading && <div style={{padding:"40px",textAlign:"center",color:"#5a7b9a"}}><span style={{width:"20px",height:"20px",border:"2px solid #333",borderTopColor:"#8aa4bd",borderRadius:"50%",display:"inline-block",animation:"spin 1s linear infinite"}}/></div>}
              <div ref={archiveLoader} style={{height:"60px"}}/>
            </>
          )}
        </div>
      </>)}

      {/* ── RESET MODAL ── */}
      {resetModal && (
        <div style={{position:"fixed",inset:0,background:"rgba(0,0,0,0.92)",display:"flex",alignItems:"center",justifyContent:"center",zIndex:300,padding:"20px",backdropFilter:"blur(8px)"}} onClick={()=>!resetLoading&&setResetModal(false)}>
          <div style={{background:"#1a2234",borderRadius:"3px",maxWidth:"480px",width:"100%",border:"1px solid #550000",boxShadow:"0 24px 80px rgba(200,0,0,0.3)"}} onClick={e=>e.stopPropagation()}>
            <div style={{padding:"28px 28px 0"}}>
              <h2 style={{margin:"0 0 12px",fontSize:"22px",color:"#ff4444"}}>⚠️ Reset All Data</h2>
              <p style={{margin:"0 0 8px",color:"#a4b8c9",fontSize:"14px"}}>This will permanently delete all posts, media, and queue data.</p>
              {!resetResult ? (<>
                <p style={{margin:"12px 0",color:"#5a7b9a",fontSize:"13px"}}>Type <strong style={{color:"#ff4444",fontFamily:"monospace"}}>RESET</strong> to confirm:</p>
                <input type="text" autoComplete="off" spellCheck={false} value={resetInput} onChange={e=>setResetInput(e.target.value)}
                  onKeyDown={e=>e.key==="Enter"&&resetInput==="RESET"&&!resetLoading&&doReset()}
                  placeholder="RESET" style={{width:"100%",boxSizing:"border-box",padding:"12px 16px",borderRadius:"3px",border:`1px solid ${resetInput==="RESET"?"#ff4444":"#2d4156"}`,background:"#131b2e",color:"#f5f7fa",fontSize:"16px",fontFamily:"monospace",outline:"none",marginBottom:"20px"}}/>
              </>) : (
                <div style={{background:"#0a1a0a",border:"1px solid #1a4a1a",borderRadius:"3px",padding:"16px",marginBottom:"20px",fontSize:"13px",color:"#46d160"}}>
                  {resetResult.error ? <span style={{color:"#ff4444"}}>Error: {resetResult.error}</span> : <>✓ Reset complete — deleted {resetResult.deleted_files} files</>}
                </div>
              )}
            </div>
            <div style={{padding:"0 28px 28px",display:"flex",gap:"10px",justifyContent:"flex-end"}}>
              <button onClick={()=>setResetModal(false)} disabled={resetLoading} style={{padding:"12px 24px",background:"#161d2f",border:"1px solid #333",borderRadius:"3px",color:"#8aa4bd",cursor:"pointer",fontSize:"14px"}}>{resetResult?"Close":"Cancel"}</button>
              {!resetResult && <button onClick={doReset} disabled={resetInput!=="RESET"||resetLoading} style={{padding:"12px 24px",background:resetInput==="RESET"?"#cc0000":"#330000",border:"1px solid #550000",borderRadius:"3px",color:resetInput==="RESET"?"#f5f7fa":"#5a7b9a",cursor:resetInput==="RESET"?"pointer":"not-allowed",fontSize:"14px",fontWeight:"600"}}>{resetLoading?"Resetting…":"Confirm Reset"}</button>}
            </div>
          </div>
        </div>
      )}

      {/* ── DELETE POST MODAL ── */}
      {deleteModal && (
        <div style={{position:"fixed",inset:0,background:"rgba(0,0,0,0.92)",display:"flex",alignItems:"center",justifyContent:"center",zIndex:300,padding:"20px",backdropFilter:"blur(8px)"}} onClick={()=>setDeleteModal(false)}>
          <div style={{background:"#1a2234",borderRadius:"3px",maxWidth:"420px",width:"100%",border:"1px solid #550000"}} onClick={e=>e.stopPropagation()}>
            <div style={{padding:"28px 28px 0"}}>
              <h2 style={{margin:"0 0 12px",fontSize:"22px",color:"#ff4444"}}>🗑️ Delete Post</h2>
              <p style={{margin:"0 0 20px",color:"#a4b8c9",fontSize:"14px"}}>This will permanently delete this post and all its downloaded media.</p>
            </div>
            <div style={{padding:"0 28px 28px",display:"flex",gap:"10px",justifyContent:"flex-end"}}>
              <button onClick={()=>setDeleteModal(false)} style={{padding:"12px 24px",background:"#161d2f",border:"1px solid #333",borderRadius:"3px",color:"#8aa4bd",cursor:"pointer",fontSize:"14px"}}>Cancel</button>
              <button onClick={confirmDeletePost} style={{padding:"12px 24px",background:"#cc0000",border:"1px solid #550000",borderRadius:"3px",color:"#f5f7fa",cursor:"pointer",fontSize:"14px",fontWeight:"600"}}>Delete</button>
            </div>
          </div>
        </div>
      )}

      {/* ── POST DETAIL MODAL ── */}
      {selectedPost && (
        <div role="dialog" aria-modal="true" aria-label={selectedPost.title}
          className="post-modal-container"
          onClick={()=>setSelectedPost(null)}>
          <div className={`modal-enter post-modal-content ${(!selectedPost.is_video && !selectedPost.video_url && !selectedPost.url && !selectedPost.image_urls?.[0]) ? 'no-media' : ''}`}
            onClick={e=>e.stopPropagation()} onTouchStart={handleTouchStart} onTouchMove={handleTouchMove} onTouchEnd={handleTouchEnd}>
            <div className="mobile-only-drag-handle" style={{display:"flex",alignItems:"center",justifyContent:"center",padding:"12px 0 4px"}}>
              <div style={{width:"40px",height:"4px",background:"#2d4156",borderRadius:"2px"}}/>
            </div>
            {(selectedPost.is_video || selectedPost.video_url) ? (
              <div className="post-modal-media">
                {selectedPost.video_url && (selectedPost.video_url.includes("v.redd.it")||selectedPost.video_url.endsWith(".mp4")) ? (
                  <video src={selectedPost.video_url} controls autoPlay muted loop playsInline style={{display:"block",background:"#000"}}/>
                ) : (
                  <div style={{minHeight:"200px",display:"flex",alignItems:"center",justifyContent:"center",flexDirection:"column",gap:"16px",padding:"40px"}}>
                    <div style={{width:"80px",height:"80px",borderRadius:"50%",background:"rgba(255,69,0,0.15)",border:"2px solid rgba(255,69,0,0.4)",display:"flex",alignItems:"center",justifyContent:"center"}}>
                      <div style={{width:0,height:0,borderTop:"16px solid transparent",borderBottom:"16px solid transparent",borderLeft:"26px solid #35c5f4",marginLeft:"6px"}}/>
                    </div>
                    {(selectedPost.video_urls?.[0] || selectedPost.url) && <a href={selectedPost.video_urls?.[0] || selectedPost.url} target="_blank" rel="noopener noreferrer" style={{color:"#35c5f4",fontSize:"13px",textDecoration:"none"}}>↗ Open video source</a>}
                  </div>
                )}
              </div>
            ) : (selectedPost.url || selectedPost.image_urls?.[0]) ? (
              <div className="post-modal-media" style={{userSelect:"none"}}>
                <img src={selectedPost.image_urls?.[galleryIdx] || selectedPost.url || selectedPost.image_urls?.[0]} alt={selectedPost.title}
                  style={{display:"block"}} onError={e=>e.target.style.display="none"} draggable={false}/>
                {selectedPost.image_urls?.length > 1 && (<>
                  <button aria-label="Previous" onClick={e=>{e.stopPropagation();setGalleryIdx(i=>Math.max(0,i-1))}} disabled={galleryIdx===0}
                    style={{position:"absolute",top:"50%",left:"8px",transform:"translateY(-50%)",zIndex:10,background:"rgba(0,0,0,0.7)",border:"1px solid rgba(255,255,255,0.15)",borderRadius:"50%",width:"48px",height:"48px",cursor:"pointer",fontSize:"24px",color:galleryIdx===0?"rgba(255,255,255,0.2)":"#f5f7fa",display:"flex",alignItems:"center",justifyContent:"center"}}>‹</button>
                  <button aria-label="Next" onClick={e=>{e.stopPropagation();setGalleryIdx(i=>Math.min(selectedPost.image_urls.length-1,i+1))}} disabled={galleryIdx===selectedPost.image_urls.length-1}
                    style={{position:"absolute",top:"50%",right:"8px",transform:"translateY(-50%)",zIndex:10,background:"rgba(0,0,0,0.7)",border:"1px solid rgba(255,255,255,0.15)",borderRadius:"50%",width:"48px",height:"48px",cursor:"pointer",fontSize:"24px",color:galleryIdx===selectedPost.image_urls.length-1?"rgba(255,255,255,0.2)":"#f5f7fa",display:"flex",alignItems:"center",justifyContent:"center"}}>›</button>
                  <div style={{position:"absolute",bottom:"12px",left:"50%",transform:"translateX(-50%)",display:"flex",gap:"5px",zIndex:10}}>
                    {selectedPost.image_urls.slice(0,10).map((_,i)=>(
                      <button key={i} onClick={e=>{e.stopPropagation();setGalleryIdx(i)}}
                        style={{width:i===galleryIdx?"20px":"7px",height:"7px",borderRadius:"3px",border:"none",background:i===galleryIdx?"#35c5f4":"rgba(255,255,255,0.4)",cursor:"pointer",padding:0,transition:"width 0.25s, background 0.25s"}}/>
                    ))}
                  </div>
                  <div style={{position:"absolute",top:"12px",left:"50%",transform:"translateX(-50%)",background:"rgba(0,0,0,0.8)",borderRadius:"3px",padding:"4px 12px",fontSize:"12px",color:"#f5f7fa",fontVariantNumeric:"tabular-nums"}}>{galleryIdx+1} / {selectedPost.image_urls.length}</div>
                </>)}
                <div style={{position:"absolute",top:"12px",right:"12px"}}>
                  <a href={selectedPost.image_urls?.[galleryIdx]||selectedPost.url} target="_blank" rel="noopener noreferrer" style={{background:"rgba(0,0,0,0.75)",color:"#f5f7fa",padding:"8px 14px",borderRadius:"3px",textDecoration:"none",fontSize:"12px",display:"flex",alignItems:"center",gap:"4px",border:"1px solid rgba(255,255,255,0.1)"}}>↗ Open</a>
                </div>
              </div>
            ) : null}

            <div className="post-modal-info" style={{padding:"20px 24px"}}>
              <div style={{display:"flex",gap:"12px",fontSize:"13px",color:"#5a7b9a",marginBottom:"16px",flexWrap:"wrap",alignItems:"center"}}>
                <span style={{color:"#35c5f4",fontWeight:"600",background:"rgba(255,69,0,0.12)",padding:"4px 10px",borderRadius:"3px",fontSize:"12px"}}>r/{selectedPost.subreddit||"reddit"}</span>
                <span style={{color:"#8aa4bd",fontSize:"12px"}}>u/{selectedPost.author||"unknown"}</span>
                {selectedPost.created_utc && <span style={{color:"#3a5068",fontSize:"11px"}}>{formatTime(selectedPost.created_utc)}</span>}
              </div>
              <h2 style={{margin:"0 0 20px",fontSize:"20px",lineHeight:"1.4",fontWeight:"600",color:"#f5f7fa"}}>{selectedPost.title}</h2>
              {selectedPost.selftext && (
                <div style={{background:"linear-gradient(145deg,#141414,#1a1a1a)",padding:"20px",borderRadius:"3px",marginBottom:"20px",fontSize:"14px",lineHeight:"1.8",color:"#b0c4d4",whiteSpace:"pre-wrap",border:"1px solid #222",maxHeight:"240px",overflow:"auto"}}>{selectedPost.selftext}</div>
              )}
              <div style={{marginBottom:"20px",display:"flex",gap:"8px",flexWrap:"wrap"}}>
                {role === "admin" && (
                  <>
                    <button onClick={()=>deletePost(selectedPost.id)} style={{padding:"11px 18px",background:"#3a1a1a",border:"1px solid #5a2a2a",borderRadius:"3px",color:"#ff6666",cursor:"pointer",fontSize:"13px",fontWeight:"600",minHeight:"44px"}}>🗑 Delete</button>
                    {selectedPost.hidden ? (
                      <button onClick={()=>unhidePost(selectedPost.id)} style={{padding:"11px 18px",background:"#1e3a1e",border:"1px solid #2a5a2a",borderRadius:"3px",color:"#46d160",cursor:"pointer",fontSize:"13px",fontWeight:"600",minHeight:"44px"}}>↩ Unhide</button>
                    ) : (
                      <button onClick={()=>hidePost(selectedPost.id)} style={{padding:"11px 18px",background:"#161d2f",border:"1px solid #333",borderRadius:"3px",color:"#8aa4bd",cursor:"pointer",fontSize:"13px",fontWeight:"600",minHeight:"44px"}}>👁 Hide</button>
                    )}
                  </>
                )}
                <button onClick={()=>setSelectedPost(null)} style={{padding:"11px 18px",background:"#161d2f",border:"1px solid #2a2a2a",borderRadius:"3px",color:"#5a7b9a",cursor:"pointer",fontSize:"13px",marginLeft:role==="admin"?"auto":0,minHeight:"44px"}}>✕ Close</button>
              </div>
              {selectedPost.comments === undefined && <div style={{color:"#3a5068",fontSize:"13px",padding:"8px 0"}}>Loading comments…</div>}
              {selectedPost.comments && selectedPost.comments.length > 0 && (
                <div>
                  <div style={{fontSize:"12px",color:"#5a7b9a",fontWeight:"600",textTransform:"uppercase",marginBottom:"12px"}}>Comments ({selectedPost.comments.length})</div>
                  <div style={{display:"flex",flexDirection:"column",gap:"8px"}}>
                    {selectedPost.comments.map(c=>(
                      <div key={c.id} style={{background:"#131b2e",borderRadius:"3px",padding:"12px",border:"1px solid #1e1e1e"}}>
                        <div style={{display:"flex",gap:"8px",alignItems:"center",marginBottom:"6px"}}>
                          <span style={{color:"#35c5f4",fontSize:"12px",fontWeight:"600"}}>u/{c.author||"[deleted]"}</span>
                          {c.created_utc && <span style={{color:"#3a5068",fontSize:"11px"}}>{formatTime(c.created_utc)}</span>}
                        </div>
                        <div style={{color:"#b0c4d4",fontSize:"14px",lineHeight:"1.6",whiteSpace:"pre-wrap"}}>{c.body}</div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {selectedPost.comments && selectedPost.comments.length === 0 && <div style={{color:"#3a5068",fontSize:"13px"}}>No comments.</div>}
            </div>
          </div>
        </div>
      )}

      {/* ── TOASTS ── */}
      <div role="status" aria-live="polite" style={{position:"fixed",bottom:"max(24px, env(safe-area-inset-bottom, 24px))",left:"50%",transform:"translateX(-50%)",display:"flex",flexDirection:"column",gap:"8px",zIndex:1000,pointerEvents:"none",width:"min(400px, calc(100vw - 32px))"}}>
        {toasts.map(t=>(
          <div key={t.id} style={{
            background:t.type==="success"?"linear-gradient(135deg,#0d2818,#1a1a1a)":t.type==="error"?"linear-gradient(135deg,#2d0a00,#1a1a1a)":"#1c2a3f",
            border:`1px solid ${t.type==="success"?"#46d16066":t.type==="error"?"#35c5f466":"#2d4156"}`,
            color:t.type==="success"?"#46d160":t.type==="error"?"#5fd4f8":"#c8d6e0",
            padding:"12px 20px",borderRadius:"3px",fontSize:"14px",boxShadow:"0 8px 32px rgba(0,0,0,0.5)",
            animation:"slideUp 0.25s ease",display:"flex",alignItems:"center",gap:"10px",pointerEvents:"auto"
          }}>
            <span aria-hidden="true">{t.type==="success"?"✓":t.type==="error"?"✗":"ⓘ"}</span>
            {t.message}
          </div>
        ))}
      </div>
      <style>{`@keyframes slideUp{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}`}</style>

      {/* ── PWA INSTALL BANNER ── */}
      {showInstallBanner && installPrompt && (
        <div style={{position:"fixed",bottom:"max(80px, calc(env(safe-area-inset-bottom, 0px) + 80px))",right:"16px",background:"linear-gradient(135deg,#1e1e1e,#141414)",border:"1px solid #35c5f444",borderRadius:"3px",padding:"14px 16px",display:"flex",alignItems:"center",gap:"12px",zIndex:900,boxShadow:"0 8px 32px rgba(0,0,0,0.4)",maxWidth:"280px",animation:"slideUp 0.3s ease"}}>
          <img src="/icon.png" style={{width:"36px",height:"36px",borderRadius:"3px"}} alt=""/>
          <div style={{flex:1}}>
            <div style={{fontSize:"13px",fontWeight:"600",color:"#f5f7fa",marginBottom:"2px"}}>Install App</div>
            <div style={{fontSize:"11px",color:"#5a7b9a"}}>Add to home screen</div>
          </div>
          <button onClick={async()=>{installPrompt.prompt();await installPrompt.userChoice;setShowInstallBanner(false);setInstallPrompt(null)}}
            style={{padding:"7px 14px",background:"linear-gradient(135deg,#35c5f4,#5fd4f8)",border:"none",borderRadius:"3px",color:"#f5f7fa",cursor:"pointer",fontSize:"12px",fontWeight:"600"}}>Install</button>
          <button onClick={()=>setShowInstallBanner(false)} style={{padding:"7px 10px",background:"#161d2f",border:"1px solid #333",borderRadius:"3px",color:"#5a7b9a",cursor:"pointer",fontSize:"12px"}}>✕</button>
        </div>
      )}
    </div>
      </div>
  )
}

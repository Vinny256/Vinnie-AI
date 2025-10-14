document.addEventListener("DOMContentLoaded", function() {
  // --- Sidebar logic (unchanged) ---
  var mobileToggle = document.getElementById("mobileSidebarToggle");
  var sidebar = document.getElementById("sidebar");
  var closeX = document.getElementById("sidebarCloseX");
  if (mobileToggle && sidebar) {
    mobileToggle.addEventListener("click", function(e) {
      e.preventDefault();
      sidebar.classList.add("active");
      document.body.classList.add("sidebar-open");
    });
    document.body.addEventListener("click", function(e) {
      if (
        document.body.classList.contains("sidebar-open") &&
        !sidebar.contains(e.target) &&
        e.target !== mobileToggle &&
        !mobileToggle.contains(e.target)
      ) {
        sidebar.classList.remove("active");
        document.body.classList.remove("sidebar-open");
      }
    });
    sidebar.addEventListener("click", function(e) {
      if (e.target.classList.contains("history-item") || e.target.classList.contains("btn-auth")) {
        sidebar.classList.remove("active");
        document.body.classList.remove("sidebar-open");
      }
    });
  }
  if (closeX && sidebar) {
    closeX.addEventListener("click", function(e) {
      e.preventDefault();
      sidebar.classList.remove("active");
      document.body.classList.remove("sidebar-open");
    });
  }

  // Markdown rendering for history
  document.querySelectorAll('.message[data-markdown="true"]').forEach(function(el){
    var strong = el.querySelector('strong');
    var html = '';
    if(strong){
      html = strong.outerHTML;
      var rest = '';
      var found = false;
      el.childNodes.forEach(function(node){
        if(found) rest += node.textContent || '';
        if(node === strong) found = true;
      });
      html += (window.DOMPurify ? DOMPurify.sanitize(marked.parse(rest.trim())) : marked.parse(rest.trim()));
      el.innerHTML = html;
    } else {
      el.innerHTML = (window.DOMPurify ? DOMPurify.sanitize(marked.parse(el.textContent)) : marked.parse(el.textContent));
    }
  });

  // Suggestion button handler
  document.querySelectorAll('.suggestion-button').forEach(function(btn){
    btn.addEventListener('click', function(e){
      e.preventDefault();
      document.getElementById('promptInput').value = btn.textContent;
      document.getElementById('promptInput').focus();
    });
  });

  // Chat handler
  var form = document.getElementById("multimodalForm");
  var chatWindow = document.getElementById("chatWindow");
  form.addEventListener("submit", function(event){
    event.preventDefault();
    var prompt = document.getElementById("promptInput").value;
    var fileInput = document.getElementById("fileInput");
    var file = fileInput.files[0];

    document.getElementById("promptInput").value = "";
    fileInput.value = "";
    document.getElementById("fileStatus").textContent = "Sending...";

    var userDiv = document.createElement("div");
    userDiv.className = "message user-message";
    userDiv.innerHTML = `<strong>You:</strong> ${(window.DOMPurify ? DOMPurify.sanitize(marked.parse(prompt)) : marked.parse(prompt))}`;
    chatWindow.appendChild(userDiv);
    chatWindow.scrollTop = chatWindow.scrollHeight;

    var thinkingDiv = document.createElement("div");
    thinkingDiv.className = "message model-message";
    thinkingDiv.setAttribute("id", "thinkingBubble");
    thinkingDiv.innerHTML = `<strong>Vinnie AI:</strong> <em>Thinking...</em>`;
    chatWindow.appendChild(thinkingDiv);
    chatWindow.scrollTop = chatWindow.scrollHeight;

    var formData = new FormData();
    formData.append("prompt", prompt);
    if(file) formData.append("file", file);

    fetch("/api/gemini-prompt", {
      method: "POST",
      body: formData
    })
    .then(function(response){ return response.text(); })
    .then(function(reply){
      document.getElementById("fileStatus").textContent = "No file attached.";
      var oldThinking = document.getElementById("thinkingBubble");
      if(oldThinking) oldThinking.remove();
      var modelDiv = document.createElement("div");
      modelDiv.className = "message model-message";
      modelDiv.innerHTML = `<strong>Vinnie AI:</strong> ${(window.DOMPurify ? DOMPurify.sanitize(marked.parse(reply)) : marked.parse(reply))}`;
      chatWindow.appendChild(modelDiv);
      chatWindow.scrollTop = chatWindow.scrollHeight;
    })
    .catch(function(error){
      document.getElementById("fileStatus").textContent = "Error sending message.";
      var oldThinking = document.getElementById("thinkingBubble");
      if(oldThinking) oldThinking.remove();
      console.error("Error:", error);
    });
  });

  // MODAL logic
  document.querySelectorAll('.btn-auth').forEach(btn => {
    btn.addEventListener('click', function(e) {
      e.preventDefault();
      if (btn.textContent.includes('Sign Up')) {
        document.getElementById('signupModal').style.display = 'flex';
      } else {
        document.getElementById('loginModal').style.display = 'flex';
      }
    });
  });
  document.getElementById('closeLoginModal').onclick = function(e){ 
    e.preventDefault();
    document.getElementById('loginModal').style.display = "none"; 
  }
  document.getElementById('closeSignupModal').onclick = function(e){ 
    e.preventDefault();
    document.getElementById('signupModal').style.display = "none"; 
  }
  document.getElementById('showLoginLink').onclick = function(e){ 
    e.preventDefault();
    document.getElementById('signupModal').style.display = "none";
    document.getElementById('loginModal').style.display = "flex";
  }
  document.getElementById('showSignupLink').onclick = function(e){
    e.preventDefault();
    document.getElementById('loginModal').style.display = "none";
    document.getElementById('signupModal').style.display = "flex";
  }
  window.onclick = function(event) {
    if (event.target.classList && event.target.classList.contains('auth-modal')) {
      event.target.style.display = "none";
    }
  };

  // Signup submit
  document.getElementById('signupForm').onsubmit = function(e){
    e.preventDefault();
    var form = e.target;
    var formData = new FormData(form);

    fetch('/signup', {
      method: 'POST',
      body: formData
    })
    .then(function(resp) {
      if (resp.redirected) {
        window.location.href = resp.url; // reload as new user
      } else {
        window.location.reload();
      }
    });
  };
  // Login submit
  document.getElementById('loginForm').onsubmit = function(e){
    e.preventDefault();
    var form = e.target;
    var formData = new FormData(form);

    fetch('/login', {
      method: 'POST',
      body: formData
    })
    .then(function(resp) {
      if (resp.redirected) {
        window.location.href = resp.url;
      } else {
        window.location.reload();
      }
    });
  };
  // Logout button logic
  const logoutBtn = document.getElementById('logoutBtn');
  if (logoutBtn) {
    logoutBtn.addEventListener('click', function(e){
      e.preventDefault();
      fetch('/logout', { method: 'GET' })
        .then(() => window.location.reload());
    });
  }
  // Ensure UI updates if already logged in (after reload)
  if (document.body.dataset.loggedIn === "true") {
    var sec = document.querySelector(".auth-section"); if(sec) sec.style.display = "none";
    var info = document.querySelector(".user-info"); if(info) info.style.display = "";
  }
});

document.getElementById('newChatBtn').addEventListener('click', function() {
  fetch('/new_chat', { method: 'POST' })
    .then(resp => resp.json())
    .then(data => window.location.reload());
});

document.querySelectorAll('.history-item').forEach(function(item) {
  item.addEventListener('click', function() {
    const chatId = item.getAttribute('data-chatid');
    document.querySelectorAll('.session-container').forEach(function(container) {
      container.style.display = (container.id === 'session-' + chatId) ? '' : 'none';
    });
    // Optionally highlight active chat
    document.querySelectorAll('.history-item').forEach(function(i) {
      i.classList.remove('active');
    });
    item.classList.add('active');
  });
});

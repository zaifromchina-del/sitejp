document.addEventListener("DOMContentLoaded", () => {
  const body = document.body;
  const userId = body.dataset.userId;
  const companyUrl = body.dataset.companyUrl;
  const passwordUrl = body.dataset.passwordUrl;
  const defaultAvatar = body.dataset.defaultAvatar;
  const avatarKey = `sitejp_avatar_${userId}`;

  const navButtons = document.querySelectorAll("[data-settings-tab]");
  const panels = document.querySelectorAll("[data-settings-panel]");
  const avatarTop = document.getElementById("settingsAvatarTop");
  const avatarPreview = document.getElementById("settingsAvatarPreview");

  function setFeedback(element, message, type = "success") {
    element.hidden = false;
    element.textContent = message;
    element.classList.toggle("success", type === "success");
    element.classList.toggle("error", type === "error");
  }

  async function postJson(url, payload) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok) {
      throw new Error(data.error || "Falha na operação.");
    }
    return data;
  }

  function showPanel(tab) {
    navButtons.forEach((button) => {
      button.classList.toggle("is-active", button.dataset.settingsTab === tab);
    });
    panels.forEach((panel) => {
      const active = panel.dataset.settingsPanel === tab;
      panel.hidden = !active;
      panel.classList.toggle("is-active", active);
    });
  }

  navButtons.forEach((button) => {
    button.addEventListener("click", () => showPanel(button.dataset.settingsTab));
  });

  const savedAvatar = localStorage.getItem(avatarKey);
  if (savedAvatar) {
    avatarTop.src = savedAvatar;
    avatarPreview.src = savedAvatar;
  }

  document.getElementById("avatarInput").addEventListener("change", (event) => {
    const file = event.target.files && event.target.files[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = () => {
      const src = reader.result || defaultAvatar;
      avatarTop.src = src;
      avatarPreview.src = src;
      localStorage.setItem(avatarKey, src);
    };
    reader.readAsDataURL(file);
  });

  document.getElementById("btnRemoveAvatar").addEventListener("click", () => {
    localStorage.removeItem(avatarKey);
    avatarTop.src = defaultAvatar;
    avatarPreview.src = defaultAvatar;
    document.getElementById("avatarInput").value = "";
  });

  document.getElementById("companyForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const feedback = document.getElementById("companyFeedback");
    const empresa = document.getElementById("empresa").value.trim();
    feedback.hidden = true;

    try {
      const data = await postJson(companyUrl, { empresa });
      document.querySelector(".settings-account-card strong").textContent = data.empresa;
      setFeedback(feedback, "Empresa atualizada com sucesso.");
    } catch (error) {
      setFeedback(feedback, error.message, "error");
    }
  });

  document.getElementById("passwordForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const feedback = document.getElementById("passwordFeedback");
    feedback.hidden = true;

    try {
      await postJson(passwordUrl, {
        senha_atual: document.getElementById("senhaAtual").value,
        senha_nova: document.getElementById("senhaNova").value,
        confirmar: document.getElementById("senhaConf").value
      });
      event.target.reset();
      setFeedback(feedback, "Senha alterada com sucesso.");
    } catch (error) {
      setFeedback(feedback, error.message, "error");
    }
  });
});

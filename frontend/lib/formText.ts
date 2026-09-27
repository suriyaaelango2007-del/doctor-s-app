import type { Language } from "./api";

/**
 * Intake-form wording in English, Tamil and Hindi.
 * The Tamil and Hindi text should be reviewed by native speakers before real patients use it.
 */
export interface FormText {
  title: string;
  intro: (doctor: string, when: string) => string;
  hello: (name: string) => string;
  mainProblem: string;
  duration: string;
  durationHint: string;
  severity: string;
  mild: string;
  moderate: string;
  severe: string;
  medicines: string;
  allergies: string;
  pastTreatments: string;
  more: string;
  patientQuestions: string;
  patientQuestionsHint: string;
  optional: string;
  listHint: string;
  submit: string;
  sending: string;
  required: string;
  doneTitle: string;
  doneBody: string;
  safety: string;
  language: string;
}

export const FORM_TEXT: Record<Language, FormText> = {
  en: {
    title: "A short form before your visit",
    hello: (name) => `Hello ${name},`,
    intro: (doctor, when) =>
      `Your appointment with ${doctor} is on ${when}. Please answer these questions so the doctor is ready for you.`,
    mainProblem: "What is the main problem you want to see the doctor about?",
    duration: "How long have you had it?",
    durationHint: "e.g. 2 weeks",
    severity: "How severe is it?",
    mild: "Mild",
    moderate: "Moderate",
    severe: "Severe",
    medicines: "Are you taking any medicines now? Which ones?",
    allergies: "Do you have any allergies?",
    pastTreatments: "Have you had any treatment for this before?",
    more: "A few more questions",
    patientQuestions: "Is there anything you would like to ask the doctor?",
    patientQuestionsHint: "One question per line",
    optional: "optional",
    listHint: "Separate with commas. Write “none” if none.",
    submit: "Send to the doctor",
    sending: "Sending…",
    required: "Please describe the main problem and choose how severe it is.",
    doneTitle: "Thank you!",
    doneBody: "Your answers have been sent to the doctor. See you at your appointment.",
    safety:
      "For medical advice, please speak to the doctor at your visit. In an emergency, go to the nearest hospital.",
    language: "Language",
  },
  ta: {
    title: "உங்கள் வருகைக்கு முன் ஒரு சிறிய படிவம்",
    hello: (name) => `வணக்கம் ${name},`,
    intro: (doctor, when) =>
      `${doctor} அவர்களுடன் உங்கள் சந்திப்பு ${when}. மருத்துவர் தயாராக இருக்க இந்தக் கேள்விகளுக்குப் பதிலளிக்கவும்.`,
    mainProblem: "நீங்கள் மருத்துவரைப் பார்க்க விரும்பும் முக்கிய பிரச்சனை என்ன?",
    duration: "இது எவ்வளவு காலமாக இருக்கிறது?",
    durationHint: "எ.கா. 2 வாரங்கள்",
    severity: "இது எவ்வளவு தீவிரமாக உள்ளது?",
    mild: "லேசானது",
    moderate: "மிதமானது",
    severe: "கடுமையானது",
    medicines: "நீங்கள் தற்போது ஏதேனும் மருந்துகள் எடுத்துக்கொள்கிறீர்களா? எவை?",
    allergies: "உங்களுக்கு ஏதேனும் ஒவ்வாமை உள்ளதா?",
    pastTreatments: "இதற்கு முன் ஏதேனும் சிகிச்சை பெற்றுள்ளீர்களா?",
    more: "இன்னும் சில கேள்விகள்",
    patientQuestions: "மருத்துவரிடம் ஏதேனும் கேட்க விரும்புகிறீர்களா?",
    patientQuestionsHint: "ஒரு வரிக்கு ஒரு கேள்வி",
    optional: "விருப்பத்தேர்வு",
    listHint: "காற்புள்ளியால் பிரிக்கவும். இல்லையென்றால் “இல்லை” என்று எழுதவும்.",
    submit: "மருத்துவருக்கு அனுப்பவும்",
    sending: "அனுப்புகிறது…",
    required: "முக்கிய பிரச்சனையை விவரித்து, அதன் தீவிரத்தைத் தேர்ந்தெடுக்கவும்.",
    doneTitle: "நன்றி!",
    doneBody: "உங்கள் பதில்கள் மருத்துவருக்கு அனுப்பப்பட்டன. சந்திப்பில் சந்திப்போம்.",
    safety:
      "மருத்துவ ஆலோசனைக்கு, உங்கள் சந்திப்பில் மருத்துவரிடம் பேசவும். அவசர நிலையில், அருகிலுள்ள மருத்துவமனைக்குச் செல்லவும்.",
    language: "மொழி",
  },
  hi: {
    title: "आपकी मुलाक़ात से पहले एक छोटा फ़ॉर्म",
    hello: (name) => `नमस्ते ${name},`,
    intro: (doctor, when) =>
      `${doctor} के साथ आपकी अपॉइंटमेंट ${when} को है। कृपया इन सवालों के जवाब दें ताकि डॉक्टर तैयार रहें।`,
    mainProblem: "आप डॉक्टर को किस मुख्य समस्या के लिए दिखाना चाहते हैं?",
    duration: "यह समस्या कब से है?",
    durationHint: "जैसे 2 हफ़्ते",
    severity: "यह कितनी गंभीर है?",
    mild: "हल्की",
    moderate: "मध्यम",
    severe: "गंभीर",
    medicines: "क्या आप अभी कोई दवा ले रहे हैं? कौन-सी?",
    allergies: "क्या आपको किसी चीज़ से एलर्जी है?",
    pastTreatments: "क्या इसके लिए पहले कोई इलाज करवाया है?",
    more: "कुछ और सवाल",
    patientQuestions: "क्या आप डॉक्टर से कुछ पूछना चाहते हैं?",
    patientQuestionsHint: "हर लाइन में एक सवाल",
    optional: "वैकल्पिक",
    listHint: "कॉमा से अलग करें। कुछ न हो तो “नहीं” लिखें।",
    submit: "डॉक्टर को भेजें",
    sending: "भेजा जा रहा है…",
    required: "कृपया मुख्य समस्या लिखें और उसकी गंभीरता चुनें।",
    doneTitle: "धन्यवाद!",
    doneBody: "आपके जवाब डॉक्टर को भेज दिए गए हैं। अपॉइंटमेंट पर मिलते हैं।",
    safety: "चिकित्सा सलाह के लिए अपनी अपॉइंटमेंट पर डॉक्टर से बात करें। आपात स्थिति में नज़दीकी अस्पताल जाएँ।",
    language: "भाषा",
  },
};

/** Specialty questions arrive in English (backend config); show them in the form's language when known. */
const SPECIALTY_TRANSLATIONS: Record<string, Partial<Record<Language, string>>> = {
  "Where on your body is the problem?": {
    ta: "உங்கள் உடலில் எந்த இடத்தில் பிரச்சனை உள்ளது?",
    hi: "आपके शरीर में समस्या कहाँ है?",
  },
  "Is it itchy or painful?": {
    ta: "அது அரிக்கிறதா அல்லது வலிக்கிறதா?",
    hi: "क्या इसमें खुजली है या दर्द?",
  },
  "Has it spread?": {
    ta: "அது பரவியுள்ளதா?",
    hi: "क्या यह फैल गई है?",
  },
};

export function specialtyQuestion(question: string, lang: Language): string {
  return SPECIALTY_TRANSLATIONS[question]?.[lang] ?? question;
}

const LOCALES: Record<Language, string> = { en: "en-IN", ta: "ta-IN", hi: "hi-IN" };

/** "2026-09-28" + "10:30:00" -> localized "Monday, 28 September, 10:30 AM" */
export function formatWhen(isoDate: string, hms: string, lang: Language): string {
  const [y, m, d] = isoDate.split("-").map(Number);
  const [h, min] = hms.split(":").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d, h, min));
  return dt.toLocaleString(LOCALES[lang], {
    weekday: "long",
    day: "numeric",
    month: "long",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
    timeZone: "UTC",
  });
}
